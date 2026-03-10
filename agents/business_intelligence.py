"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : business_intelligence
SKILL : Business Intelligence — KPI data + context → executive report, trend analysis, decision recommendations

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), kpi_data (str), period (str),
             goals (str), context (str) — immutable after entry
  Outputs  : bi_report (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All BIReportState fields are thread-scoped only.

Loop Policy:
  NONE — single-pass node.

Failure Discrimination:
  PERMANENT  → ValueError (missing client_name, kpi_data)
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
  UNEXPECTED → Exception

Checkpoint Semantics:
  PRE  — before Claude call
  POST — after completion

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics

log = structlog.get_logger()

ROLE        = "business_intelligence"
MAX_RETRIES = 3
RETRY_MIN_S = 3
RETRY_MAX_S = 45
MAX_TOKENS  = 1800
KPI_CHARS   = 4000   # KPI data can be long — allow full tables
CONTEXT_CHARS = 1500


class BIReportState(BaseState):
    # Inputs
    client_name: str    # Business or client name
    kpi_data: str       # Raw KPI data: numbers, tables, metrics (plain text or CSV format)
    period: str         # Reporting period, e.g. "March 2026", "Q1 2026", "Week 10"
    goals: str          # What targets were they trying to hit?
    context: str        # Business context, market conditions, notable events in the period
    # Output
    bi_report: str      # Structured BI report; empty on failure


def _build_bi_prompt(state: "BIReportState", persona: dict) -> str:
    context_block = (
        f"\n━━━ CONTEXT ━━━\n{state['context'][:CONTEXT_CHARS]}"
        if state.get("context", "").strip()
        else ""
    )
    goals_block = (
        f"\n━━━ GOALS / TARGETS ━━━\n{state['goals']}"
        if state.get("goals", "").strip()
        else ""
    )
    return f"""{persona['personality']}

Analyse the KPI data below and produce a precise, executive-level BI report.
Be data-led — every claim must reference a specific number from the data.
Highlight what changed, why it matters, and what to do about it. Max 700 words.

━━━ CLIENT ━━━
{state['client_name']}

━━━ PERIOD ━━━
{state.get('period') or 'Not specified'}

━━━ KPI DATA ━━━
{state['kpi_data'][:KPI_CHARS]}{goals_block}{context_block}

━━━ DELIVER ━━━

## Business Intelligence Report: {state['client_name']} — {state.get('period', 'Current Period')}

### Executive Summary
[3 sentences: performance vs target, key trend, single most important action]

### Performance vs Target
| Metric | Target | Actual | Δ | Status |
|---|---|---|---|---|
[Fill one row per KPI. Use ↑↓ symbols. Status: ✅ ON TRACK / ⚠️ AT RISK / ❌ MISSED]

### Top Trends
[3 bullet points — what is genuinely moving in the data, not just restating numbers]

### Root Cause Analysis
[For any metrics that missed target: what drove the gap? Be specific.]

### Decision Points
[2-3 decisions the leadership team should make THIS week based on this data]

### Leading Indicators
[What to watch in the next period that will predict whether trajectory improves]

### Forecast
[Projected performance next period based on current trends — conservative + optimistic]"""


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def business_intelligence_node(state: BIReportState) -> dict:
    thread_id   = state.get("workflow_id") or str(uuid.uuid4())
    client_name = state.get("client_name", "")
    persona     = get_persona(ROLE)
    notifier    = TelegramNotifier()
    state_logger = SupabaseStateLogger()
    metrics     = CallMetrics(thread_id, ROLE)

    def _checkpoint(cid: str, payload: dict) -> None:
        state_logger.log_state(thread_id, cid, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, client=client_name)

    try:
        if not client_name.strip():
            raise ValueError("client_name is required.")
        if not state.get("kpi_data", "").strip():
            raise ValueError("kpi_data is required — provide the metrics to analyse.")

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_bi_prompt(state, persona)

        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "period": state.get("period", ""),
             "status": "generating"},
        )

        bi_report = _generate(claude, prompt, metrics)
        metrics.log()
        metrics.persist()

        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "status": "completed",
             "report_chars": len(bi_report)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, report_chars=len(bi_report))
        return {"bi_report": bi_report, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"client": client_name, "status": "permanent_failure", "error": msg})
        return {"bi_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"bi_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"bi_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}
