"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : financial_analyst
SKILL : Financial Analyst

Financial Analyst - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), financial_context (str), output_type (VALID_OUTPUT_TYPES), analysis_type (VALID_ANALYSIS_TYPES)
    Outputs: financial_report (str), key_metrics (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/analysis_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before financial data analysis
    POST - financial_report + key_metrics persisted after successful generation
"""

from __future__ import annotations

from state.base import BaseState

import re
from typing import TypedDict

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE        = "financial_analyst"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "cashflow_analysis", "kpi_dashboard", "p_and_l_summary", "pricing_model",
    "budget_forecast", "unit_economics", "investor_summary", "general",
}
VALID_ANALYSIS_TYPES = {
    "saas_metrics", "ecommerce_metrics", "agency_metrics",
    "startup_metrics", "general",
}

# ── Financial KPI Frameworks ───────────────────────────────────────────────────
_KPI_SETS = {
    "saas_metrics": {
        "north_star":   "MRR (Monthly Recurring Revenue)",
        "kpis":         ["MRR", "ARR", "Churn Rate", "Net Revenue Retention", "CAC", "LTV", "LTV:CAC ratio", "Payback Period", "MRR Growth Rate"],
        "benchmarks":   {"churn": "<2% monthly", "ltv_cac": ">3:1", "payback": "<12 months", "nrr": ">100%"},
        "red_flags":    ["Churn > 5% monthly", "LTV:CAC < 2:1", "CAC payback > 18 months"],
    },
    "ecommerce_metrics": {
        "north_star":   "Revenue",
        "kpis":         ["Revenue", "GMV", "AOV", "Conversion Rate", "CAC", "ROAS", "Gross Margin", "Repeat Purchase Rate", "Refund Rate"],
        "benchmarks":   {"conversion": "2–4%", "roas": ">3x", "gross_margin": ">40%"},
        "red_flags":    ["ROAS < 2x", "Gross margin < 20%", "Refund rate > 10%"],
    },
    "agency_metrics": {
        "north_star":   "Monthly Recurring Revenue + Project Revenue",
        "kpis":         ["MRR", "Average Project Value", "Gross Margin", "Utilisation Rate", "Client Retention Rate", "Revenue per Employee", "Pipeline Value"],
        "benchmarks":   {"gross_margin": ">50%", "utilisation": "70–80%", "retention": ">85%"},
        "red_flags":    ["Utilisation < 60%", "Single client > 40% of revenue", "Gross margin < 40%"],
    },
    "startup_metrics": {
        "north_star":   "Revenue + Runway",
        "kpis":         ["Monthly Burn Rate", "Runway (months)", "Revenue", "Growth Rate MoM", "Gross Margin", "CAC", "Activation Rate"],
        "benchmarks":   {"runway": ">18 months", "growth_mom": ">10%", "gross_margin": ">60%"},
        "red_flags":    ["Runway < 6 months", "Burn > 2x revenue", "Growth < 5% MoM"],
    },
    "general": {
        "north_star":   "Revenue",
        "kpis":         ["Revenue", "Gross Profit", "Net Profit", "Cash Position", "Growth Rate"],
        "benchmarks":   {},
        "red_flags":    ["Negative gross margin", "Cash < 3 months runway"],
    },
}

_FINANCIAL_FORMULAS = {
    "LTV":           "ARPU / Churn Rate",
    "CAC":           "Total Sales & Marketing Spend / New Customers Acquired",
    "LTV:CAC":       "LTV / CAC (target > 3:1)",
    "Gross Margin":  "(Revenue - COGS) / Revenue × 100",
    "Burn Rate":     "Total Monthly Expenses - Monthly Revenue",
    "Runway":        "Cash Balance / Monthly Net Burn",
    "ROAS":          "Revenue from Ads / Ad Spend",
    "Payback Period":"CAC / (ARPU × Gross Margin %)",
}


class FinancialAnalystState(BaseState):
    workflow_id:       str
    timestamp:         str
    agent:             str
    error:             str | None
    task:              str
    financial_context: str
    output_type:       str
    analysis_type:     str
    financial_report:  str
    key_metrics:       str


# ── Phase 1 - Financial Signal Detection (pure, no Claude) ────────────────────
def _detect_financial_signals(task: str, analysis_type: str) -> dict:
    """Returns financial_data dict - pure lookup and heuristics."""
    kpi_set    = _KPI_SETS.get(analysis_type, _KPI_SETS["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if any(w in task_lower for w in ["churn", "retention", "cancel"]):
        flags.append("Churn analysis - segment by cohort, age, and plan tier")
    if any(w in task_lower for w in ["burn", "runway", "cash"]):
        flags.append("Cash management - 18-month projection minimum")
    if any(w in task_lower for w in ["investor", "raise", "funding"]):
        flags.append("Investor framing - lead with growth rate + TAM + LTV:CAC")
    if any(w in task_lower for w in ["pricing", "price", "tier"]):
        flags.append("Pricing - anchor high, justify value, test willingness to pay")
    if any(w in task_lower for w in ["forecast", "projection", "plan"]):
        flags.append("Forecasting - base / optimistic / pessimistic scenarios required")

    return {
        "kpi_set":   kpi_set,
        "formulas":  _FINANCIAL_FORMULAS,
        "flags":     flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Financial Report ─────────────────────────────────────────
def _build_financial_prompt(state: FinancialAnalystState, fin_data: dict) -> str:
    persona       = get_persona(ROLE)
    task          = state["task"]
    fin_ctx       = state.get("financial_context", "")
    out_type      = state.get("output_type", "general")
    analysis_type = state.get("analysis_type", "general")
    kpi_set       = fin_data["kpi_set"]

    flags_text     = "\n".join(f"  ⚡ {f}" for f in fin_data["flags"]) or "  None detected"
    kpis_text      = ", ".join(kpi_set["kpis"])
    benchmarks_txt = "\n".join(f"  {k}: {v}" for k, v in kpi_set["benchmarks"].items()) or "  General benchmarks apply"
    red_flags_txt  = "\n".join(f"  🚨 {r}" for r in kpi_set["red_flags"])
    formulas_txt   = "\n".join(f"  {k} = {v}" for k, v in fin_data["formulas"].items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Produce a {out_type} for {analysis_type} business model.

NORTH STAR METRIC: {kpi_set['north_star']}
KEY KPIs TO TRACK: {kpis_text}

INDUSTRY BENCHMARKS:
{benchmarks_txt}

RED FLAGS TO WATCH:
{red_flags_txt}

FINANCIAL FORMULAS:
{formulas_txt}

ANALYSIS FLAGS:
{flags_text}

TASK:
{task}

FINANCIAL CONTEXT / DATA:
{fin_ctx or "None provided - provide framework and guidance for gathering the right data"}

OUTPUT FORMAT:
## Financial Analysis: {out_type.replace('_',' ').title()} - {analysis_type}

### Executive Summary
[3 bullet points: current position, biggest opportunity, biggest risk]

### KPI Dashboard
| Metric | Current | Target | Benchmark | Status |
|---|---|---|---|---|
[rows for all relevant KPIs]

### Key Findings
[Numbered - each with: observation, implication, recommended action]

### Scenario Analysis (if forecasting)
| Scenario | Revenue 3M | Revenue 6M | Revenue 12M | Key Assumption |
|---|---|---|---|---|
[Base / Optimistic / Pessimistic]

### Red Flag Assessment
[Each red flag: present / not present / unknown - with evidence]

### Recommendations
[Top 3 - prioritised by financial impact, specific and actionable]

### Next Action
[Single highest-ROI financial action right now]

KEY_METRICS: [comma-separated: metric=value pairs from the analysis]
"""

_build_prompt = _build_financial_prompt  # spec alias


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


def financial_analyst_node(state: FinancialAnalystState) -> FinancialAnalystState:
    thread_id     = state.get("workflow_id", "local")
    task          = state.get("task", "").strip()
    out_type      = state.get("output_type", "general")
    analysis_type = state.get("analysis_type", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if analysis_type not in VALID_ANALYSIS_TYPES:
        raise ValueError(f"PERMANENT: analysis_type '{analysis_type}' not in {VALID_ANALYSIS_TYPES}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "analysis_type": analysis_type})
    fin_data = _detect_financial_signals(task, analysis_type)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_financial_prompt(state, fin_data)

    try:
        report = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    km_match    = re.search(r'KEY_METRICS:\s*(.+)', report)
    key_metrics = km_match.group(1).strip() if km_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "analysis_type": analysis_type})

    return {**state, "agent": ROLE, "financial_report": report, "key_metrics": key_metrics, "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(FinancialAnalystState)
    g.add_node("financial_analyst", financial_analyst_node)
    g.set_entry_point("financial_analyst")
    g.add_edge("financial_analyst", END)
    return g.compile()
