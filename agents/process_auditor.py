"""
Process Friction Detector — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : process_description (str), process_type (VALID_PROCESS_TYPES), output_type (VALID_OUTPUT_TYPES)
    Outputs: audit_report (str), friction_count (int), bottleneck_score (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty description, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty description, unknown process_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before friction signal detection
    POST — audit_report + friction_count persisted after successful generation
"""

from __future__ import annotations

import re
from typing import TypedDict

import anthropic
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint

ROLE        = "process_auditor"
MAX_RETRIES = 3
MAX_TOKENS  = 2000

VALID_PROCESS_TYPES = {
    "deployment", "development_workflow", "client_onboarding", "content_pipeline",
    "sales_process", "support_workflow", "automation", "general",
}
VALID_OUTPUT_TYPES = {"friction_report", "improvement_plan", "process_map", "audit_summary"}

# ── Friction Signal Patterns ──────────────────────────────────────────────────
_FRICTION_PATTERNS = [
    (r'(manual(ly)?|by hand|someone has to)',           "manual_step",      8),
    (r'(wait(ing)? for|pending approval|blocked on)',   "approval_blocker", 9),
    (r'(copy[- ]paste|copy and paste)',                 "manual_transfer",  7),
    (r'(email(ing)? (the|a) (file|document|report))',   "file_by_email",    6),
    (r'(duplicate|duplicating|do it again|redo)',        "duplication",      7),
    (r'(unclear|ambiguous|not sure who|no owner)',      "ownership_gap",    8),
    (r'(takes (too long|ages|forever|days?))',          "time_sink",        9),
    (r'(single point of failure|only [A-Za-z]+ knows)', "bus_factor",       10),
    (r'(no (test|check|review|validation))',            "no_quality_gate",  8),
    (r'(spreadsheet|excel|google sheet)',               "spreadsheet_ops",  5),
]

_PROCESS_DIMENSIONS = {
    "deployment":           ["automation_level", "rollback_plan", "monitoring", "zero_downtime"],
    "development_workflow": ["pr_review_time", "ci_cd_coverage", "test_gates", "documentation"],
    "client_onboarding":    ["handoff_clarity", "automation", "communication", "timeline"],
    "content_pipeline":     ["brief_to_publish_time", "review_stages", "repurposing", "scheduling"],
    "sales_process":        ["lead_qualification", "follow_up_automation", "crm_usage", "close_rate"],
    "support_workflow":     ["triage_speed", "escalation_path", "knowledge_base", "resolution_time"],
    "automation":           ["trigger_reliability", "error_handling", "monitoring", "fallback"],
    "general":              ["clarity", "automation", "ownership", "feedback_loops"],
}


class ProcessAuditorState(TypedDict, total=False):
    workflow_id:         str
    timestamp:           str
    agent:               str
    error:               str | None
    process_description: str
    process_type:        str
    output_type:         str
    audit_report:        str
    friction_count:      int
    bottleneck_score:    int


# ── Phase 1 — Friction Detection (pure, no Claude) ────────────────────────────
def _detect_friction_signals(description: str) -> tuple[int, list[tuple], int]:
    """Returns (friction_count, signals[(label, severity, excerpt)], bottleneck_score_0_10)."""
    signals: list[tuple] = []
    total_severity = 0
    for pattern, label, severity in _FRICTION_PATTERNS:
        matches = re.findall(pattern, description, re.IGNORECASE)
        if matches:
            excerpt = matches[0] if isinstance(matches[0], str) else matches[0][0]
            signals.append((label, severity, excerpt))
            total_severity += severity
    friction_count    = len(signals)
    bottleneck_score  = min(10, round(total_severity / max(friction_count, 1))) if signals else 0
    return friction_count, signals, bottleneck_score

_build_prompt = None  # assigned below


# ── Phase 2 — Claude Process Audit ──────────────────────────────────────────────
def _build_process_prompt(state: ProcessAuditorState, friction_count: int, signals: list, bottleneck_score: int) -> str:
    persona      = get_persona(ROLE)
    description  = state["process_description"]
    process_type = state.get("process_type", "general")
    output_type  = state.get("output_type", "friction_report")
    dimensions   = _PROCESS_DIMENSIONS.get(process_type, _PROCESS_DIMENSIONS["general"])

    signals_text = "
".join(
        f"  [{sev}/10] {label}: '{excerpt}'" for label, sev, excerpt in signals
    ) if signals else "  None detected by regex."

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Detect process friction and deliver a concrete improvement plan.

PROCESS TYPE: {process_type}
OUTPUT TYPE: {output_type}
KEY DIMENSIONS TO EVALUATE: {', '.join(dimensions)}

REGEX PRE-SCAN:
  Friction signals found: {friction_count}
  Bottleneck severity (0–10): {bottleneck_score}
  Signals:
{signals_text}

PROCESS DESCRIPTION:
"""
{description[:4000]}
"""

YOUR TASK:
1. Identify every friction point — manual steps, approval bottlenecks, duplication, ownership gaps.
2. Score each friction point (1–10 severity, 1–10 fix effort).
3. Rank the top 3 highest-leverage improvements.
4. Provide a concrete action for each — tool, automation, or process change.
5. Map the improved process flow (before vs after).

OUTPUT FORMAT:
## Process Friction Audit
**Process Type:** {process_type}
**Output Type:** {output_type}
**Total Friction Points:** [N]
**Bottleneck Score:** [0–10]

### Friction Inventory
| # | Friction Point | Severity (1–10) | Fix Effort (1–10) | Category |
|---|---|---|---|---|
[table rows]

### Top 3 High-Leverage Fixes
1. **[Fix Name]** — [exact action, tool/automation, expected outcome]
2. **[Fix Name]** — [exact action, tool/automation, expected outcome]
3. **[Fix Name]** — [exact action, tool/automation, expected outcome]

### Before vs After Process Flow
**Before:** [numbered steps, current state]
**After:** [numbered steps, improved state]

### Automation Opportunities
[List each step that can be automated with suggested tool]

### Verdict
[LOW FRICTION — optimise edges | MODERATE — fix top 3 | HIGH FRICTION — process redesign needed]

BOTTLENECK_SCORE: {bottleneck_score}
"""

_build_prompt = _build_process_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code in (429, 529)
    return False


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _audit_process(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    metrics.log()
    metrics.persist()
    return response.content[0].text


def process_auditor_node(state: ProcessAuditorState) -> ProcessAuditorState:
    thread_id           = state.get("workflow_id", "local")
    process_description = state.get("process_description", "").strip()
    process_type        = state.get("process_type", "general")
    output_type         = state.get("output_type", "friction_report")

    if not process_description:
        raise ValueError("PERMANENT: process_description is required.")
    if process_type not in VALID_PROCESS_TYPES:
        raise ValueError(f"PERMANENT: process_type '{process_type}' not in {VALID_PROCESS_TYPES}")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")

    checkpoint("PRE", thread_id, ROLE, {"process_type": process_type, "output_type": output_type})

    friction_count, signals, bottleneck_score = _detect_friction_signals(process_description)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_process_prompt(state, friction_count, signals, bottleneck_score)

    try:
        report = _audit_process(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    checkpoint("POST", thread_id, ROLE, {
        "friction_count": friction_count, "bottleneck_score": bottleneck_score,
    })

    return {
        **state,
        "agent":            ROLE,
        "audit_report":     report,
        "friction_count":   friction_count,
        "bottleneck_score": bottleneck_score,
        "error":            None,
    }
