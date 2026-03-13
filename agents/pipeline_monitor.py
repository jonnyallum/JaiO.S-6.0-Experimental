"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : pipeline_monitor
 SKILL : Pipeline Monitor — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : pipeline_name (str), pipeline_type (str),
               log_data (str — raw logs, error messages, or status dump),
               expected_behaviour (str — what should be happening),
               output_type (str)
 Output keys : diagnosis (str), alert_level (str), action_items (list[str])
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 signal classification →
 Phase 2 Claude diagnosis + remediation. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid pipeline_type/output_type (ValueError),
               empty pipeline_name or log_data
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: pipeline_type, alert_level,
        error_count, silent_failure_detected
 POST — logged after success: diagnosis char count, action_item count

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import re

import anthropic
import structlog
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona
from tools.supabase_tools import SupabaseStateLogger

# ── Identity ──────────────────────────────────────────────────────────────────
log = structlog.get_logger()

ROLE = "pipeline_monitor"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2000
LOG_PREVIEW = 4000   # max chars of log_data sent to Claude

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_PIPELINE_TYPES = {
    "ci_cd", "data_pipeline", "api", "webhook",
    "cron_job", "deployment", "etl", "message_queue", "general"
}
VALID_OUTPUT_TYPES = {
    "diagnosis", "incident_report", "runbook",
    "alert_config", "health_dashboard_spec", "postmortem"
}

# ── Alert level patterns — ordered by severity ────────────────────────────────
_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, alert_level, signal_type)
    (r'\b(CRITICAL|FATAL|PANIC|OOM|killed|segfault)\b',            "critical",       "crash"),
    (r'\b(ERROR|Exception|Traceback|stack trace|500|503)\b',        "critical",       "error"),
    (r'\b(timeout|timed out|connection refused|ECONNREFUSED)\b',    "critical",       "connectivity"),
    (r'\b(WARNING|WARN|deprecated|fallback|retry)\b',               "warning",        "degradation"),
    (r'\b(slow|latency|p99|high memory|throttl)\b',                 "warning",        "performance"),
    (r'(200|201|OK|success|completed|done)',                        "info",           "success"),
    # Silent failure patterns — the hardest to detect
    (r'\b(0 records|no rows|empty result|skipped|noop)\b',          "silent_failure", "empty_output"),
    (r'\b(dry.?run|disabled|feature.?flag.*false)\b',               "silent_failure", "suppressed"),
]

# ── Pipeline type monitoring checklists ───────────────────────────────────────
_MONITOR_CHECKLIST: dict[str, list[str]] = {
    "ci_cd":          ["Build status", "Test pass rate", "Deployment success", "Rollback trigger", "Duration drift"],
    "data_pipeline":  ["Record count (vs expected)", "Schema drift", "Null rate", "Latency", "Idempotency"],
    "api":            ["Error rate (vs baseline)", "P99 latency", "Rate limit hits", "Auth failures", "Payload size drift"],
    "webhook":        ["Delivery success rate", "Retry count", "Payload validation failures", "Endpoint response time"],
    "cron_job":       ["Execution time (vs schedule)", "Duration vs baseline", "Exit code", "Output record count"],
    "deployment":     ["Health check status", "Rollout progress", "Error rate post-deploy", "Canary metrics"],
    "etl":            ["Source record count", "Transform errors", "Load success rate", "Duplicate detection"],
    "message_queue":  ["Queue depth", "Consumer lag", "Dead letter count", "Processing rate"],
    "general":        ["Error rate", "Throughput", "Latency", "Resource utilisation", "Output validity"],
}

# ── State ──────────────────────────────────────────────────────────────────────
class PipelineState(BaseState):
    # Inputs
    pipeline_name:        str   # name of the pipeline or service
    pipeline_type:        str   # type of pipeline
    log_data:             str   # raw logs, errors, or status dump
    expected_behaviour:   str   # what should be happening normally
    output_type:          str   # type of monitoring output
    thread_id:            str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    alert_level:           str        # info / warning / critical / silent_failure (owner: this node)
    signal_summary:        dict       # counts of signal types detected (owner: this node)
    silent_failure_detected: bool     # True if silent failure patterns found (owner: this node)
    checklist:             list[str]  # monitoring checklist for this pipeline type (owner: this node)

    # Outputs
    diagnosis:    str        # full diagnosis output (owner: this node)
    action_items: list[str]  # extracted action items (owner: this node)
    error:        str        # failure reason if any (owner: this node)


# ── Phase 1 — pure signal classification (no Claude) ─────────────────────────

def _classify_signals(log_data: str) -> tuple[str, dict, bool]:
    """
    Phase 1 — scan logs for error/warning/silent-failure patterns.
    Returns (alert_level, signal_summary, silent_failure_detected).
    Pure function — no Claude, no I/O. Independently testable.
    """
    signal_summary: dict[str, int] = {}
    highest_level = "info"
    level_rank    = {"info": 0, "warning": 1, "silent_failure": 2, "critical": 3}

    for pattern, level, sig_type in _ERROR_PATTERNS:
        matches = len(re.findall(pattern, log_data, re.IGNORECASE))
        if matches:
            signal_summary[sig_type] = signal_summary.get(sig_type, 0) + matches
            if level_rank.get(level, 0) > level_rank.get(highest_level, 0):
                highest_level = level

    silent_failure_detected = signal_summary.get("empty_output", 0) > 0 or signal_summary.get("suppressed", 0) > 0
    return highest_level, signal_summary, silent_failure_detected


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    pipeline_name: str,
    pipeline_type: str,
    log_data: str,
    expected_behaviour: str,
    output_type: str,
    alert_level: str,
    signal_summary: dict,
    silent_failure_detected: bool,
    checklist: list[str],
) -> str:
    """Pure function — assembles the monitoring brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = output_type.replace("_", " ").title()
    alert_emoji   = {"critical": "🔴", "warning": "🟡", "silent_failure": "⚠️", "info": "🟢"}.get(alert_level, "⚪")
    signal_str    = "\n".join(f"  {k}: {v} occurrences" for k, v in signal_summary.items()) if signal_summary else "  No patterns matched"
    checklist_str = "\n".join(f"  - {item}" for item in checklist)
    silent_note   = "\n⚠️ SILENT FAILURE DETECTED — pipeline appears to be running but producing no output. Prioritise this." if silent_failure_detected else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} production pipeline guardian.

Pipeline       : {pipeline_name}
Type           : {pipeline_type}
Alert level    : {alert_emoji} {alert_level.upper()}
Output type    : {output_label}{silent_note}

Expected behaviour:
{expected_behaviour}

Pre-classified signal summary:
{signal_str}

Monitoring checklist for {pipeline_type}:
{checklist_str}

Log data (last {LOG_PREVIEW} chars):
{log_data[-LOG_PREVIEW:]}

Produce a complete {output_label}:

FOR DIAGNOSIS:
1. INCIDENT SUMMARY (1 paragraph — what's happening, since when, impact)
2. ROOT CAUSE ANALYSIS (most likely cause, confidence level, evidence from logs)
3. BLAST RADIUS (what else is affected or at risk)
4. IMMEDIATE ACTIONS (numbered — do these NOW in this order)
5. INVESTIGATION STEPS (for root cause confirmation)
6. PREVENTION (what change prevents this recurring)

FOR INCIDENT_REPORT:
Standard incident report: timeline, impact, root cause, resolution, follow-up actions.

FOR RUNBOOK:
Step-by-step remediation runbook for this specific failure pattern.
Assume on-call engineer is under pressure and needs exact commands.

FOR ALERT_CONFIG:
Design the alerting rules that would have caught this earlier:
- Metric to watch | Threshold | Window | Severity | Notification channel
- For each of the monitoring checklist items

FOR HEALTH_DASHBOARD_SPEC:
Dashboard spec for this pipeline:
- Panel name | Metric | Visualisation type | Alert threshold | Refresh rate

FOR POSTMORTEM:
Blameless postmortem: timeline, contributing factors, what went well, what didn't, action items with owners.

Priority: silent failures are more dangerous than loud ones — a pipeline that silently produces wrong output is worse than one that crashes."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _diagnose(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _extract_action_items(diagnosis: str) -> list[str]:
    """Phase 1 (post) — extract numbered action items. Pure function."""
    lines = diagnosis.split("\n")
    items = []
    in_actions = False
    for line in lines:
        if "IMMEDIATE ACTIONS" in line.upper() or "ACTION ITEMS" in line.upper():
            in_actions = True
            continue
        if in_actions:
            if re.match(r'^\d+\.', line.strip()):
                items.append(line.strip())
            elif line.strip().startswith("##") or (line.strip() and not line[0].isdigit() and not line.startswith(" ")):
                if items:  # stop at next section
                    break
    return items[:10]  # cap at 10


# ── Node ───────────────────────────────────────────────────────────────────────

def pipeline_monitor_node(state: PipelineState) -> PipelineState:
    thread_id           = state.get("thread_id", "unknown")
    pipeline_name       = state.get("pipeline_name", "").strip()
    pipeline_type       = state.get("pipeline_type", "general").lower().strip()
    log_data            = state.get("log_data", "").strip()
    expected_behaviour  = state.get("expected_behaviour", "").strip()
    output_type         = state.get("output_type", "diagnosis").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not pipeline_name:
        return {**state, "error": "PERMANENT: pipeline_name is required"}
    if not log_data:
        return {**state, "error": "PERMANENT: log_data is required"}
    if not expected_behaviour:
        return {**state, "error": "PERMANENT: expected_behaviour is required"}
    if pipeline_type not in VALID_PIPELINE_TYPES:
        return {**state, "error": f"PERMANENT: pipeline_type '{pipeline_type}' not in {VALID_PIPELINE_TYPES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure signal classification ──────────────────────────────────
    alert_level, signal_summary, silent_failure_detected = _classify_signals(log_data)
    checklist = _MONITOR_CHECKLIST.get(pipeline_type, _MONITOR_CHECKLIST["general"])

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        pipeline_name, pipeline_type, log_data, expected_behaviour,
        output_type, alert_level, signal_summary, silent_failure_detected, checklist,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "pipeline_type":          pipeline_type,
        "alert_level":            alert_level,
        "error_count":            signal_summary.get("error", 0) + signal_summary.get("crash", 0),
        "silent_failure_detected": silent_failure_detected,
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        diagnosis = _diagnose(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    action_items = _extract_action_items(diagnosis)

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "diagnosis_chars":  len(diagnosis),
        "action_item_count": len(action_items),
        "alert_level":      alert_level,
    })

    return {
        **state,
        "diagnosis":               diagnosis,
        "alert_level":             alert_level,
        "action_items":            action_items,
        "signal_summary":          signal_summary,
        "silent_failure_detected": silent_failure_detected,
        "checklist":               checklist,
        "error":                   "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PipelineState)
    g.add_node("pipeline_monitor", pipeline_monitor_node)
    g.set_entry_point("pipeline_monitor")
    g.add_edge("pipeline_monitor", END)
    return g.compile()
