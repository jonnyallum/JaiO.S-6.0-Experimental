"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : customer_success
 SKILL : Customer Success — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : customer_name (str), input_text (str — ticket/message/
               feedback/survey data), request_type (str),
               customer_tier (str — optional),
               product_context (str — optional)
 Output keys : cs_output (str), sentiment (str), urgency_score (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 sentiment + urgency scoring →
 Phase 2 Claude response/plan. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid request_type/customer_tier (ValueError),
               empty customer_name or input_text
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: request_type, sentiment,
        urgency_score, customer_tier
 POST — logged after success: output char count, sentiment confirmed

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

ROLE = "customer_success"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2000

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_REQUEST_TYPES = {
    "support_triage", "churn_analysis", "onboarding_review",
    "feedback_synthesis", "escalation_plan", "nps_analysis",
    "renewal_brief", "upsell_opportunity"
}
VALID_CUSTOMER_TIERS = {"free", "starter", "pro", "enterprise", "vip", "unknown"}

# ── Sentiment signal patterns ─────────────────────────────────────────────────
_NEGATIVE_SIGNALS = [
    r'\b(frustrat|angry|terrible|awful|broken|useless|waste|refund|cancel|leave|quit|switch|competitor)\w*\b',
    r'\b(disappointed|fed up|sick of|gave up|can\'t believe|unacceptable|ridiculous)\b',
    r'(!{2,}|\?{2,})',  # multiple ! or ?
]
_POSITIVE_SIGNALS = [
    r'\b(love|great|excellent|amazing|perfect|fantastic|helpful|recommend|happy|pleased|impressed)\w*\b',
    r'\b(thank|appreciate|wonderful|outstanding|brilliant)\w*\b',
]
_URGENCY_SIGNALS = [
    r'\b(urgent|asap|immediately|critical|emergency|down|outage|blocked|can\'t work|deadline)\w*\b',
    r'\b(client.{0,20}waiting|boss.{0,20}asking|demo.{0,10}(today|tomorrow)|launch.{0,10}(today|tomorrow))\b',
]
_CHURN_SIGNALS = [
    r'\b(cancel|cancellation|leaving|churning|switching|competitor|unsubscribe|downgrade)\w*\b',
    r'\b(not worth|too expensive|cheaper alternative|found a better)\b',
]

# ── Tier SLA targets ───────────────────────────────────────────────────────────
_TIER_SLA: dict[str, dict] = {
    "free":       {"response_target": "72h", "escalation": "None — self-serve only", "priority": 1},
    "starter":    {"response_target": "24h", "escalation": "Support lead if > 48h", "priority": 2},
    "pro":        {"response_target": "8h",  "escalation": "CS Manager if > 24h",   "priority": 3},
    "enterprise": {"response_target": "2h",  "escalation": "Account Executive + CS VP if > 4h", "priority": 5},
    "vip":        {"response_target": "1h",  "escalation": "CEO awareness if > 2h", "priority": 5},
    "unknown":    {"response_target": "24h", "escalation": "Triage to determine tier first", "priority": 2},
}

# ── State ──────────────────────────────────────────────────────────────────────
class CustomerSuccessState(BaseState):
    # Inputs
    customer_name:   str   # customer name or company
    input_text:      str   # ticket, feedback, survey response, or conversation
    request_type:    str   # type of CS task
    customer_tier:   str   # customer tier for SLA routing
    product_context: str   # optional — product/plan they're on
    thread_id:       str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    sentiment:     str   # positive / neutral / negative / mixed (owner: this node)
    urgency_score: int   # 0–10 urgency score (owner: this node)
    churn_risk:    bool  # True if churn signals detected (owner: this node)
    sla_data:      dict  # SLA targets for this tier (owner: this node)

    # Outputs
    cs_output: str   # full CS response/plan (owner: this node)
    error:     str   # failure reason if any (owner: this node)


# ── Phase 1 — pure sentiment + urgency scoring (no Claude) ────────────────────

def _score_input(text: str) -> tuple[str, int, bool]:
    """
    Phase 1 — score sentiment, urgency, and churn risk from raw text.
    Returns (sentiment, urgency_score_0_10, churn_risk). Pure function.
    """
    text_lower   = text.lower()
    neg_hits     = sum(len(re.findall(p, text_lower)) for p in _NEGATIVE_SIGNALS)
    pos_hits     = sum(len(re.findall(p, text_lower)) for p in _POSITIVE_SIGNALS)
    urgency_hits = sum(len(re.findall(p, text_lower)) for p in _URGENCY_SIGNALS)
    churn_hits   = sum(len(re.findall(p, text_lower)) for p in _CHURN_SIGNALS)

    # Sentiment classification
    if neg_hits > pos_hits * 2:
        sentiment = "negative"
    elif pos_hits > neg_hits * 2:
        sentiment = "positive"
    elif neg_hits > 0 and pos_hits > 0:
        sentiment = "mixed"
    else:
        sentiment = "neutral"

    # Urgency score 0–10
    urgency_score = min(10, urgency_hits * 3 + (2 if neg_hits > 3 else 0) + (3 if churn_hits > 0 else 0))

    churn_risk = churn_hits > 0

    return sentiment, urgency_score, churn_risk


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    customer_name: str,
    input_text: str,
    request_type: str,
    customer_tier: str,
    product_context: str,
    sentiment: str,
    urgency_score: int,
    churn_risk: bool,
    sla_data: dict,
) -> str:
    """Pure function — assembles the CS brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = request_type.replace("_", " ").title()
    context_str   = f"\nProduct context: {product_context}" if product_context else ""
    urgency_label = "🔴 HIGH" if urgency_score >= 7 else ("🟡 MEDIUM" if urgency_score >= 4 else "🟢 LOW")
    churn_flag    = "\n⚠️ CHURN RISK DETECTED — address retention proactively in all outputs." if churn_risk else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} customer success specialist.

Customer       : {customer_name} ({customer_tier} tier)
Request type   : {output_label}
Sentiment      : {sentiment}
Urgency        : {urgency_label} ({urgency_score}/10){churn_flag}
SLA target     : {sla_data['response_target']} response | Escalation: {sla_data['escalation']}{context_str}

Input text:
{input_text[:2500]}

Deliver a complete {output_label}:

FOR SUPPORT_TRIAGE:
1. ISSUE CLASSIFICATION (category + sub-category + severity)
2. CUSTOMER TONE ASSESSMENT (empathy guidance for the responder)
3. DRAFT RESPONSE (ready to send — personalised, not templated)
   - Acknowledge the specific frustration/situation
   - Explain what's happening / what you're doing
   - Set clear next steps with timeframe
   - Close with confidence, not defensiveness
4. INTERNAL NOTES (for the ticket — what to investigate, who to loop in)
5. ESCALATION TRIGGER (if urgency >= 7 or churn risk — specify who and why)

FOR CHURN_ANALYSIS:
1. CHURN SIGNALS (what language/behaviour indicates risk)
2. ROOT CAUSE HYPOTHESIS (why are they likely leaving?)
3. RETENTION PLAYBOOK (3 specific interventions in order of priority)
4. SAVE OFFER (if appropriate — what to offer and how to frame it)
5. DECISION POINT (when to let them go gracefully vs fight for retention)

FOR ONBOARDING_REVIEW:
1. WHERE THEY ARE in the onboarding journey (based on their message)
2. GAPS detected (what value they haven't unlocked yet)
3. PERSONALISED NEXT STEP EMAIL (ready to send)
4. SUCCESS MILESTONE to aim for in next 7 days
5. RED FLAGS (if they're at risk of churning before activation)

FOR FEEDBACK_SYNTHESIS:
1. THEME EXTRACTION (top 3 themes from the feedback)
2. SENTIMENT BREAKDOWN (% positive/neutral/negative per theme)
3. PRODUCT INSIGHTS (specific feature requests or pain points)
4. RECOMMENDED ACTIONS (for product team, support team, comms team)
5. CUSTOMER RESPONSE (acknowledge and close the feedback loop)

FOR ESCALATION_PLAN:
1. ESCALATION JUSTIFICATION (why this needs senior attention)
2. BRIEF FOR ESCALATEE (1 paragraph — context, situation, desired outcome)
3. COMMUNICATION TO CUSTOMER (what to tell them while escalating)
4. RESOLUTION TIMELINE (committed timeline to get back to them)
5. SUCCESS CRITERIA (what "resolved" looks like for this customer)

FOR NPS_ANALYSIS:
1. SCORE INTERPRETATION (detractor/passive/promoter + what drove the score)
2. FOLLOW-UP STRATEGY (different approach per segment)
3. RECOVERY PLAN for detractors (personalised outreach template)
4. AMPLIFICATION PLAN for promoters (referral / review / case study ask)
5. PRODUCT THEMES (systemic issues vs one-offs)

FOR RENEWAL_BRIEF:
1. ACCOUNT HEALTH SUMMARY (based on the input signals)
2. VALUE DELIVERED (what they've actually used/achieved — if mentioned)
3. RISK FACTORS (what might block renewal)
4. RENEWAL CONVERSATION GUIDE (talking points, objection handling)
5. NEGOTIATION GUARDRAILS (what to offer, what not to)

FOR UPSELL_OPPORTUNITY:
1. READINESS SIGNAL (why now is the right time)
2. RECOMMENDED UPGRADE (which tier/feature and why it fits them)
3. PITCH ANGLE (personalised to their situation — not a feature dump)
4. TIMING RECOMMENDATION (when to bring it up in the conversation)
5. OBJECTION HANDLING (3 likely objections + responses)

Always use {customer_name}'s name. Never use generic templates."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _generate_cs_output(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def customer_success_node(state: CustomerSuccessState) -> CustomerSuccessState:
    thread_id       = state.get("thread_id", "unknown")
    customer_name   = state.get("customer_name", "").strip()
    input_text      = state.get("input_text", "").strip()
    request_type    = state.get("request_type", "support_triage").lower().strip()
    customer_tier   = state.get("customer_tier", "unknown").lower().strip()
    product_context = state.get("product_context", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not customer_name:
        return {**state, "error": "PERMANENT: customer_name is required"}
    if not input_text:
        return {**state, "error": "PERMANENT: input_text is required"}
    if request_type not in VALID_REQUEST_TYPES:
        return {**state, "error": f"PERMANENT: request_type '{request_type}' not in {VALID_REQUEST_TYPES}"}
    if customer_tier not in VALID_CUSTOMER_TIERS:
        return {**state, "error": f"PERMANENT: customer_tier '{customer_tier}' not in {VALID_CUSTOMER_TIERS}"}

    # ── Phase 1 — pure sentiment + urgency scoring ────────────────────────────
    sentiment, urgency_score, churn_risk = _score_input(input_text)
    sla_data = _TIER_SLA.get(customer_tier, _TIER_SLA["unknown"])

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        customer_name, input_text, request_type, customer_tier,
        product_context, sentiment, urgency_score, churn_risk, sla_data,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "request_type":  request_type,
        "sentiment":     sentiment,
        "urgency_score": urgency_score,
        "customer_tier": customer_tier,
        "churn_risk":    churn_risk,
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        cs_output = _generate_cs_output(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "output_chars":  len(cs_output),
        "sentiment":     sentiment,
        "urgency_score": urgency_score,
        "churn_risk":    churn_risk,
    })

    return {
        **state,
        "cs_output":     cs_output,
        "sentiment":     sentiment,
        "urgency_score": urgency_score,
        "churn_risk":    churn_risk,
        "sla_data":      sla_data,
        "error":         "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(CustomerSuccessState)
    g.add_node("customer_success", customer_success_node)
    g.set_entry_point("customer_success")
    g.add_edge("customer_success", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
