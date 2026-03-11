"""
Project Manager — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), project_context (str), output_type (VALID_OUTPUT_TYPES), methodology (VALID_METHODOLOGIES)
    Outputs: pm_output (str), action_items (list)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/methodology → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before project analysis
    POST — pm_output + action_items persisted after successful generation
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

ROLE        = "project_manager"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "project_plan", "sprint_plan", "task_breakdown", "status_report",
    "risk_register", "stakeholder_brief", "retrospective", "general",
}
VALID_METHODOLOGIES = {
    "agile", "kanban", "scrum", "waterfall", "shape_up", "gtd", "general",
}

# ── PM Frameworks ──────────────────────────────────────────────────────────────
_METHODOLOGY_PROFILES = {
    "agile": {
        "cadence":    "2-week sprints, daily standups, sprint review + retro",
        "artifacts":  ["backlog", "sprint board", "velocity chart", "burndown"],
        "ceremonies": ["sprint planning", "daily standup", "review", "retrospective"],
        "strengths":  "Adaptable, fast feedback loops, iterative delivery",
        "pitfalls":   "Scope creep, stakeholder alignment without clear roadmap",
    },
    "kanban": {
        "cadence":    "Continuous flow, WIP limits, weekly reviews",
        "artifacts":  ["kanban board", "cycle time", "throughput", "WIP limits"],
        "ceremonies": ["weekly review", "flow metrics review"],
        "strengths":  "Visualises work, great for support/ops teams, no sprints",
        "pitfalls":   "Without WIP limits, everything becomes urgent",
    },
    "shape_up": {
        "cadence":    "6-week cycles + 2-week cooldown",
        "artifacts":  ["pitch", "hill chart", "appetite", "scopes"],
        "ceremonies": ["betting table", "kickoff", "cooldown"],
        "strengths":  "Fixed time + variable scope, kills scope creep by design",
        "pitfalls":   "Requires trust and autonomy — won't work with micromanagement",
    },
    "general": {
        "cadence":    "Context-dependent",
        "artifacts":  ["task list", "timeline", "status update"],
        "ceremonies": ["kickoff", "weekly check-in", "closeout"],
        "strengths":  "Flexible",
        "pitfalls":   "Without structure, coordination breaks down",
    },
}

_RISK_CATEGORIES = {
    "scope":      ("Requirements unclear or expanding",     "Define acceptance criteria before build"),
    "timeline":   ("Dependency delays or underestimation",  "Add 20% buffer, identify critical path"),
    "resource":   ("Key person unavailable or overloaded",  "Cross-train, document, avoid single points"),
    "technical":  ("Unknown unknowns in implementation",    "Spike before committing to estimate"),
    "stakeholder":("Misaligned expectations or slow sign-off", "Weekly written updates, decisions in writing"),
    "quality":    ("Testing skipped under deadline pressure", "Quality gates baked into definition of done"),
}

_STATUS_SIGNALS = {
    "on_track":    r'(on track|ahead|ahead of schedule|green)',
    "at_risk":     r'(at risk|delayed|blocked|behind|amber)',
    "off_track":   r'(off track|overdue|missed|critical|red)',
    "completed":   r'(done|complete|shipped|delivered|closed)',
}


class ProjectManagerState(TypedDict, total=False):
    workflow_id:     str
    timestamp:       str
    agent:           str
    error:           str | None
    task:            str
    project_context: str
    output_type:     str
    methodology:     str
    pm_output:       str
    action_items:    list


# ── Phase 1 — Project Analysis (pure, no Claude) ──────────────────────────────
def _analyse_project(task: str, methodology: str) -> dict:
    """Returns project_data dict — pure lookup and signal detection."""
    profile    = _METHODOLOGY_PROFILES.get(methodology, _METHODOLOGY_PROFILES["general"])
    task_lower = task.lower()
    flags: list[str] = []
    risks: list[str] = []

    for category, (risk, mitigation) in _RISK_CATEGORIES.items():
        if category in task_lower:
            risks.append(f"{category.title()}: {risk} → {mitigation}")

    if "deadline" in task_lower or "urgent" in task_lower:
        flags.append("Hard deadline detected — build critical path analysis first")
    if "stakeholder" in task_lower or "client" in task_lower:
        flags.append("Stakeholder management required — written communication trail essential")
    if "team" in task_lower or "resource" in task_lower:
        flags.append("Team coordination — assign owners to every task, not groups")
    if "budget" in task_lower or "cost" in task_lower:
        flags.append("Budget tracking required — weekly spend vs forecast")

    # Detect project status from context
    status = "unknown"
    for s, pattern in _STATUS_SIGNALS.items():
        if re.search(pattern, task_lower):
            status = s
            break

    return {
        "profile":     profile,
        "flags":       flags,
        "risks":       risks,
        "status":      status,
        "risk_cats":   _RISK_CATEGORIES,
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude PM Output ─────────────────────────────────────────────────
def _build_pm_prompt(state: ProjectManagerState, proj_data: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    proj_ctx    = state.get("project_context", "")
    out_type    = state.get("output_type", "project_plan")
    methodology = state.get("methodology", "general")
    profile     = proj_data["profile"]

    flags_text = "
".join(f"  ⚡ {f}" for f in proj_data["flags"]) or "  None detected"
    risks_text = "
".join(f"  ⚠ {r}" for r in proj_data["risks"]) or "  No specific risks detected in context"
    arts_text  = ", ".join(profile["artifacts"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Produce a {out_type} using {methodology} methodology.

METHODOLOGY PROFILE:
  Cadence:    {profile['cadence']}
  Artifacts:  {arts_text}
  Ceremonies: {', '.join(profile['ceremonies'])}
  Strengths:  {profile['strengths']}
  Pitfalls:   {profile['pitfalls']}

PROJECT STATUS: {proj_data['status'].replace('_',' ').upper()}

PROJECT FLAGS:
{flags_text}

IDENTIFIED RISKS:
{risks_text}

TASK:
{task}

PROJECT CONTEXT:
{proj_ctx or "None provided"}

OUTPUT FORMAT:
## Project Plan: {out_type.replace('_',' ').title()} — {methodology}

### Project Overview
[Objective, scope, success criteria — 3 bullet points each]

### Timeline
| Phase | Duration | Key Deliverable | Owner | Status |
|---|---|---|---|---|
[rows]

### Task Breakdown
[Numbered tasks with: owner, estimate, dependencies, acceptance criteria]

### Risk Register
| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
[rows — minimum 3]

### Action Items (next 48 hours)
[Numbered list — each: specific task, owner, due date]

### Communication Plan
[Who gets what update, when, in what format]

### Definition of Done
[Specific criteria — not "complete" but measurable]

ACTION_ITEMS: [comma-separated list of the next 3 immediate actions]
"""

_build_prompt = _build_pm_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-opus-4-6", max_tokens=MAX_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def project_manager_node(state: ProjectManagerState) -> ProjectManagerState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    out_type    = state.get("output_type", "project_plan")
    methodology = state.get("methodology", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if methodology not in VALID_METHODOLOGIES:
        raise ValueError(f"PERMANENT: methodology '{methodology}' not in {VALID_METHODOLOGIES}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "methodology": methodology})
    proj_data = _analyse_project(task, methodology)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_pm_prompt(state, proj_data)

    try:
        output = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    ai_match     = re.search(r'ACTION_ITEMS:\s*(.+)', output)
    action_items = [a.strip() for a in ai_match.group(1).split(",")] if ai_match else []

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "methodology": methodology})

    return {**state, "agent": ROLE, "pm_output": output, "action_items": action_items, "error": None}
