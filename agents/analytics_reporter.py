"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : analytics_reporter
 SKILL : Analytics Reporter — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : raw_data (dict | str), focus (str), period (str),
               goal (str — optional business objective)
 Output keys : analytics_report (str), key_metrics (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 trend computation →
 Phase 2 Claude narrative. PARSE_ATTEMPTS = 2 (metrics extraction).

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid focus/period (ValueError), empty raw_data,
               data schema unrecognisable after PARSE_ATTEMPTS
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: focus, period, computed trend keys
 POST — logged after success: report char count, key_metrics keys

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import json
import re
from typing import Any, Optional

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

ROLE = "analytics_reporter"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES    = 3
MAX_TOKENS     = 2000
PARSE_ATTEMPTS = 2

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_FOCUS   = {"traffic", "conversion", "revenue", "engagement", "retention", "general"}
VALID_PERIODS = {"7d", "30d", "90d", "ytd", "custom"}

# ── State ──────────────────────────────────────────────────────────────────────
class AnalyticsState(BaseState):
    # Inputs
    raw_data:  Any     # dict or JSON string of metric data
    focus:     str     # analysis focus area
    period:    str     # reporting period
    goal:      str     # optional business objective
    thread_id: str     # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    trends:     dict   # % changes, MoM, anomaly flags (owner: this node)
    data_ready: bool   # Phase 1 succeeded (owner: this node)

    # Outputs
    analytics_report: str   # narrative report (owner: this node)
    key_metrics:      dict  # structured metric summary (owner: this node)
    error:            str   # failure reason if any (owner: this node)


# ── Phase 1 — pure trend computation (no Claude) ──────────────────────────────

def _safe_float(val: Any) -> Optional[float]:
    """Coerce a value to float, returning None on failure."""
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _pct_change(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


def _compute_trends(raw_data: Any) -> tuple[dict, dict]:
    """
    Phase 1 — extract numeric metrics and compute period-over-period trends.
    Returns (trends: dict, key_metrics: dict). Pure function — no Claude.
    """
    # Normalise input to dict
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            # Try to extract key:value pairs from freeform text
            data = {}
            for match in re.finditer(r'"?(\w[\w\s]*?)"?\s*[:=]\s*([0-9,.]+)', raw_data):
                key = match.group(1).strip().lower().replace(" ", "_")
                val = _safe_float(match.group(2))
                if val is not None:
                    data[key] = val
    else:
        data = dict(raw_data) if raw_data else {}

    if not data:
        return {}, {}

    key_metrics: dict = {}
    trends:      dict = {}

    # Walk all keys looking for numeric values and *_prev counterparts
    for key, val in data.items():
        fval = _safe_float(val)
        if fval is None:
            continue
        key_metrics[key] = fval
        prev_key = f"{key}_prev"
        if prev_key in data:
            prev_val = _safe_float(data[prev_key])
            if prev_val is not None:
                change = _pct_change(fval, prev_val)
                if change is not None:
                    trends[f"{key}_change_pct"] = change
                    trends[f"{key}_direction"] = "up" if change > 0 else ("down" if change < 0 else "flat")
                    # Anomaly flag: > 50% swing
                    if abs(change) > 50:
                        trends[f"{key}_anomaly"] = True

    return trends, key_metrics


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(focus: str, period: str, goal: str, raw_data: Any, trends: dict) -> str:
    """Pure function — assembles the analyst prompt from Phase 1 outputs."""
    persona = get_persona(ROLE)
    goal_str = f"\nBusiness objective: {goal}" if goal else ""

    trend_lines = "\n".join(
        f"  {k}: {v}" for k, v in trends.items()
    ) if trends else "  No comparative data available."

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} analytics specialist.

Reporting period : {period}
Analysis focus   : {focus}{goal_str}

Raw data provided:
{json.dumps(raw_data, indent=2) if isinstance(raw_data, dict) else str(raw_data)[:3000]}

Pre-computed trend signals:
{trend_lines}

Deliver a structured analytics report with these sections:
1. EXECUTIVE SUMMARY (3 bullet points — the most important takeaways)
2. KEY METRICS (table: metric | value | vs prior period | status)
3. TRENDS & PATTERNS (what is moving and why)
4. ANOMALIES & ALERTS (flag anything unusual)
5. RECOMMENDATIONS (3–5 concrete next actions ranked by impact)

Be specific with numbers. No fluff. Truth-lock every claim to the data provided."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _generate_report(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


# ── Node ───────────────────────────────────────────────────────────────────────

def analytics_reporter_node(state: AnalyticsState) -> AnalyticsState:
    thread_id = state.get("thread_id", "unknown")
    focus     = state.get("focus", "general").lower().strip()
    period    = state.get("period", "30d").lower().strip()
    raw_data  = state.get("raw_data")
    goal      = state.get("goal", "")

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if focus not in VALID_FOCUS:
        return {**state, "error": f"PERMANENT: focus '{focus}' not in {VALID_FOCUS}"}
    if period not in VALID_PERIODS:
        return {**state, "error": f"PERMANENT: period '{period}' not in {VALID_PERIODS}"}
    if not raw_data:
        return {**state, "error": "PERMANENT: raw_data is empty — nothing to analyse"}

    # ── Phase 1 — pure trend computation ──────────────────────────────────────
    attempts = 0
    trends, key_metrics = {}, {}
    while attempts < PARSE_ATTEMPTS:
        try:
            trends, key_metrics = _compute_trends(raw_data)
            break
        except Exception:
            attempts += 1
            if attempts >= PARSE_ATTEMPTS:
                return {**state, "error": "PERMANENT: data schema unrecognisable after PARSE_ATTEMPTS"}

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(focus, period, goal, raw_data, trends)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "focus": focus, "period": period,
        "trend_keys": list(trends.keys()),
        "metric_count": len(key_metrics),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        report = _generate_report(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "report_chars": len(report),
        "key_metric_keys": list(key_metrics.keys()),
        "trend_count": len(trends),
    })

    return {
        **state,
        "analytics_report": report,
        "key_metrics": key_metrics,
        "data_ready": True,
        "trends": trends,
        "error": "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AnalyticsState)
    g.add_node("analytics_reporter", analytics_reporter_node)
    g.set_entry_point("analytics_reporter")
    g.add_edge("analytics_reporter", END)
    return g.compile()
