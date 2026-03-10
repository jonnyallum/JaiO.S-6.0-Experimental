"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 launch_orchestrator — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), launch_type (str), channels (str),
               launch_date (str — ISO YYYY-MM-DD), audience (str),
               current_date (str — ISO, optional — defaults to today)
 Output keys : launch_plan (str), timeline (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 T-minus computation →
 Phase 2 Claude plan. DURATION_LIMIT = 90 days (max planning window).

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid launch_type/channels (ValueError), launch_date
               in the past, T-minus > DURATION_LIMIT days
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: launch_type, channels, t_minus_days
 POST — logged after success: plan char count, timeline milestone count

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import anthropic
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona

# ── Identity ──────────────────────────────────────────────────────────────────
ROLE = "launch_orchestrator"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES    = 3
MAX_TOKENS     = 2200
DURATION_LIMIT = 90   # max days before launch date we'll plan for

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_LAUNCH_TYPES = {"product", "feature", "campaign", "event", "partnership", "rebrand"}
VALID_CHANNELS     = {"social", "email", "paid", "pr", "seo", "community", "all"}

# ── Phase templates — per-type milestone phases ────────────────────────────────
_PHASE_MAP: dict[str, list[tuple[int, str]]] = {
    "product": [
        (-30, "Teaser & waitlist open"),
        (-14, "Beta invite wave + press embargo lift"),
        (-7,  "Final countdown — social blitz + email warm-up"),
        (-3,  "Pre-launch media outreach"),
        (0,   "LAUNCH DAY — all channels live"),
        (3,   "Post-launch momentum push"),
        (7,   "First-week review + retargeting activation"),
        (14,  "Social proof harvest + case study brief"),
    ],
    "feature": [
        (-14, "Internal stakeholder brief"),
        (-7,  "Changelog draft + email announcement prep"),
        (-2,  "Social teasers + in-app notification prep"),
        (0,   "LAUNCH DAY — changelog live + email send"),
        (3,   "User education content push"),
        (7,   "Adoption metrics review"),
    ],
    "campaign": [
        (-21, "Creative brief locked + assets in production"),
        (-14, "Ad accounts structured + targeting configured"),
        (-7,  "Soft test spend + creative review"),
        (-1,  "Final creative approval"),
        (0,   "CAMPAIGN LIVE — full budget active"),
        (7,   "First-week optimisation review"),
        (14,  "Scale or cut decision point"),
    ],
    "event": [
        (-60, "Save-the-date send"),
        (-30, "Full invite + registration open"),
        (-14, "Reminder sequence activated"),
        (-7,  "Speaker/agenda announcement"),
        (-1,  "Final reminder + logistics email"),
        (0,   "EVENT DAY"),
        (3,   "Follow-up sequence + recording share"),
    ],
    "partnership": [
        (-21, "Partner brief + co-marketing assets"),
        (-14, "Joint announcement draft approved"),
        (-7,  "Partner social brief + scheduling"),
        (0,   "ANNOUNCEMENT DAY — joint channels live"),
        (7,   "Cross-promotion activation"),
        (14,  "Results review with partner"),
    ],
    "rebrand": [
        (-30, "Internal rollout + asset freeze"),
        (-14, "Press & partner embargo brief"),
        (-7,  "Social teaser — 'something new is coming'"),
        (-3,  "Domain/redirect + email prep"),
        (0,   "REBRAND LIVE — all assets switched"),
        (7,   "FAQ + brand story content"),
        (14,  "Sentiment & reach review"),
    ],
}

# ── State ──────────────────────────────────────────────────────────────────────
class LaunchState(TypedDict):
    # Inputs
    product_name: str   # name of product/feature/event being launched
    launch_type:  str   # type of launch
    channels:     str   # comma-separated channels or "all"
    launch_date:  str   # ISO date string YYYY-MM-DD
    audience:     str   # target audience description
    current_date: str   # ISO date string (optional — defaults to today)
    thread_id:    str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    t_minus_days: int   # days until launch (owner: this node)
    timeline:     dict  # milestone dict keyed by date string (owner: this node)
    active_channels: list  # resolved channel list (owner: this node)

    # Outputs
    launch_plan: str   # full launch plan (owner: this node)
    error:       str   # failure reason if any (owner: this node)


# ── Phase 1 — pure timeline computation (no Claude) ───────────────────────────

def _resolve_channels(channels_str: str) -> list[str]:
    if channels_str.strip().lower() == "all":
        return list(VALID_CHANNELS - {"all"})
    return [c.strip().lower() for c in channels_str.split(",") if c.strip().lower() in VALID_CHANNELS]


def _build_timeline(launch_date_str: str, launch_type: str, current_date_str: str = "") -> tuple[int, dict]:
    """
    Phase 1 — pure date arithmetic. Returns (t_minus_days, timeline_dict).
    No Claude, no I/O — independently testable.
    """
    launch_dt  = date.fromisoformat(launch_date_str)
    today      = date.fromisoformat(current_date_str) if current_date_str else date.today()
    t_minus    = (launch_dt - today).days

    phases     = _PHASE_MAP.get(launch_type, _PHASE_MAP["product"])
    timeline   = {}

    for offset, label in phases:
        milestone_date = launch_dt.__class__.fromordinal(launch_dt.toordinal() + offset)
        # Only include milestones in the future (or today)
        if milestone_date >= today:
            timeline[milestone_date.isoformat()] = label

    return t_minus, timeline


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product_name: str,
    launch_type: str,
    audience: str,
    t_minus_days: int,
    timeline: dict,
    active_channels: list,
) -> str:
    """Pure function — assembles the launch brief from Phase 1 outputs."""
    persona = get_persona(ROLE)

    timeline_str = "\n".join(
        f"  {dt}: {label}" for dt, label in sorted(timeline.items())
    ) if timeline else "  (launch date reached — focus on post-launch execution)"

    channels_str = ", ".join(active_channels) if active_channels else "all channels"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} go-to-market specialist.

Product / Initiative : {product_name}
Launch type          : {launch_type}
Target audience      : {audience}
Days until launch    : {t_minus_days}
Active channels      : {channels_str}

Pre-computed T-minus milestones:
{timeline_str}

Build a comprehensive GTM launch plan with:

1. LAUNCH STRATEGY (3 bullet objectives — what success looks like)
2. T-MINUS TIMELINE (expand each milestone with specific deliverables and owners)
3. CHANNEL PLAYBOOK (per-channel: content type, frequency, KPI to track)
4. LAUNCH DAY RUNSHEET (hour-by-hour if < 7 days out, else key moments)
5. RISK REGISTER (top 3 risks with mitigation actions)
6. SUCCESS METRICS (primary KPI + 3 supporting metrics with targets)

Be specific. No generic advice. Every action must be executable."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _plan_launch(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def launch_orchestrator_node(state: LaunchState) -> LaunchState:
    thread_id    = state.get("thread_id", "unknown")
    product_name = state.get("product_name", "").strip()
    launch_type  = state.get("launch_type", "").lower().strip()
    channels     = state.get("channels", "all").lower().strip()
    launch_date  = state.get("launch_date", "").strip()
    audience     = state.get("audience", "").strip()
    current_date = state.get("current_date", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product_name:
        return {**state, "error": "PERMANENT: product_name is required"}
    if launch_type not in VALID_LAUNCH_TYPES:
        return {**state, "error": f"PERMANENT: launch_type '{launch_type}' not in {VALID_LAUNCH_TYPES}"}
    if not launch_date:
        return {**state, "error": "PERMANENT: launch_date is required (ISO YYYY-MM-DD)"}

    # ── Phase 1 — pure timeline computation ───────────────────────────────────
    try:
        t_minus_days, timeline = _build_timeline(launch_date, launch_type, current_date)
    except ValueError as exc:
        return {**state, "error": f"PERMANENT: invalid date format — {exc}"}

    if t_minus_days < 0:
        return {**state, "error": f"PERMANENT: launch_date is in the past (T-{abs(t_minus_days)} days)"}
    if t_minus_days > DURATION_LIMIT:
        return {**state, "error": f"PERMANENT: T-minus {t_minus_days} days exceeds DURATION_LIMIT={DURATION_LIMIT}"}

    active_channels = _resolve_channels(channels)
    if not active_channels:
        return {**state, "error": f"PERMANENT: no valid channels in '{channels}' — choose from {VALID_CHANNELS}"}

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(product_name, launch_type, audience, t_minus_days, timeline, active_channels)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "launch_type": launch_type,
        "t_minus_days": t_minus_days,
        "channels": active_channels,
        "milestone_count": len(timeline),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        plan = _plan_launch(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "plan_chars": len(plan),
        "milestone_count": len(timeline),
        "t_minus_days": t_minus_days,
    })

    return {
        **state,
        "launch_plan":      plan,
        "timeline":         timeline,
        "t_minus_days":     t_minus_days,
        "active_channels":  active_channels,
        "error":            "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(LaunchState)
    g.add_node("launch_orchestrator", launch_orchestrator_node)
    g.set_entry_point("launch_orchestrator")
    g.add_edge("launch_orchestrator", END)
    return g.compile()
