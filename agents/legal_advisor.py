"""
Legal Advisor - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), legal_context (str), output_type (VALID_OUTPUT_TYPES), jurisdiction (VALID_JURISDICTIONS)
    Outputs: legal_advice (str), risk_level (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/jurisdiction → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before legal risk analysis
    POST - legal_advice + risk_level persisted after successful generation
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
from langgraph.graph import StateGraph, END

ROLE        = "legal_advisor"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "gdpr_review", "contract_review", "ip_assessment", "compliance_checklist",
    "terms_of_service", "privacy_policy", "data_processing_agreement", "general",
}
VALID_JURISDICTIONS = {
    "uk", "eu", "us", "us_california", "australia", "canada", "general",
}

_DISCLAIMER = "IMPORTANT: This is AI-generated legal guidance for informational purposes only. It does not constitute legal advice. Consult a qualified solicitor or attorney before acting on this information."

# ── Legal Framework Library ────────────────────────────────────────────────────
_GDPR_REQUIREMENTS = [
    "Lawful basis for processing must be documented (Art. 6)",
    "Privacy notice must be clear, accessible, and complete (Art. 13/14)",
    "Data subject rights: access, erasure, portability, rectification (Art. 15–20)",
    "Data minimisation - collect only what is necessary (Art. 5)",
    "Purpose limitation - use data only for stated purposes (Art. 5)",
    "Consent must be freely given, specific, informed, unambiguous (Art. 7)",
    "DPA required for all processors (Art. 28)",
    "72-hour breach notification to ICO (Art. 33)",
    "DPIA required for high-risk processing (Art. 35)",
    "Data retention limits must be defined and enforced (Art. 5)",
]

_IP_CHECKLIST = [
    "Trademark search before brand/product launch",
    "Copyright assignment clauses in contractor agreements",
    "Open-source licence compatibility check (GPL vs MIT vs Apache)",
    "Trade secrets: NDA before any disclosure",
    "Domain squatting: register .com + .co.uk + social handles on day 1",
    "Employee IP assignment in employment contracts",
]

_CONTRACT_RED_FLAGS = [
    "Unlimited liability clauses - always cap at contract value",
    "IP ownership unclear - must state who owns deliverables",
    "No termination clause - define notice periods and conditions",
    "Auto-renewal with no notice period",
    "Jurisdiction in an unfavourable territory",
    "Payment terms > 30 days without interest on late payment",
    "Non-compete scope too broad - unenforceable in many jurisdictions",
]

_RISK_SIGNALS = {
    "high":   [r'(lawsuit|litigation|breach|violation|penalty|fine|GDPR|ICO|FTC)'],
    "medium": [r'(contract|agreement|IP|copyright|trademark|NDA|liability)'],
    "low":    [r'(terms|privacy|policy|compliance|review|audit)'],
}

_JURISDICTION_NOTES = {
    "uk":          "UK GDPR (post-Brexit) mirrors EU GDPR. ICO is the supervisory authority. Companies Act 2006 applies.",
    "eu":          "EU GDPR - EDPB oversees. Member state DPAs enforce locally. DSA/DMA also relevant for platforms.",
    "us":          "No federal privacy law yet. FTC enforces. State laws vary: CA (CCPA/CPRA), VA, CO, TX active.",
    "us_california":"CCPA/CPRA applies to businesses meeting thresholds. Right to opt out of sale of personal data.",
    "australia":   "Privacy Act 1988 + Australian Privacy Principles. OAIC is the regulator.",
    "canada":       "PIPEDA federally + provincial laws (Quebec Law 25 most stringent). OPC enforces.",
    "general":      "Jurisdiction not specified - flag where local law review is required.",
}


class LegalAdvisorState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    legal_context: str
    output_type:   str
    jurisdiction:  str
    legal_advice:  str
    risk_level:    str


# ── Phase 1 - Risk Signal Detection (pure, no Claude) ─────────────────────────
def _detect_legal_risks(task: str, legal_context: str) -> dict:
    """Returns legal_data dict - pure pattern matching and lookups."""
    combined  = (task + " " + legal_context).lower()
    risk_level = "low"
    for level in ["high", "medium", "low"]:
        for pattern in _RISK_SIGNALS[level]:
            if re.search(pattern, combined, re.IGNORECASE):
                risk_level = level
                break
        if risk_level == "high":
            break

    flags: list[str] = []
    if re.search(r'(personal data|user data|email|name|address|payment)', combined):
        flags.append("Personal data processing detected - GDPR/privacy law applies")
    if re.search(r'(contractor|freelancer|agency|work for hire)', combined):
        flags.append("Contractor relationship - IP assignment clause essential")
    if re.search(r'(open.?source|licence|MIT|GPL|Apache)', combined, re.IGNORECASE):
        flags.append("Open-source licence in scope - check compatibility and attribution")
    if re.search(r'(children|under 13|under 16|COPPA|KOSA)', combined):
        flags.append("Children's data - COPPA (US) / GDPR Article 8 (EU) - heightened obligations")

    return {
        "risk_level":      risk_level,
        "flags":           flags,
        "gdpr_reqs":       _GDPR_REQUIREMENTS,
        "ip_checklist":    _IP_CHECKLIST,
        "contract_flags":  _CONTRACT_RED_FLAGS,
        "disclaimer":      _DISCLAIMER,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Legal Advice ──────────────────────────────────────────────
def _build_legal_prompt(state: LegalAdvisorState, legal_data: dict) -> str:
    persona      = get_persona(ROLE)
    task         = state["task"]
    legal_ctx    = state.get("legal_context", "")
    out_type     = state.get("output_type", "general")
    jurisdiction = state.get("jurisdiction", "general")
    juris_note   = _JURISDICTION_NOTES.get(jurisdiction, _JURISDICTION_NOTES["general"])

    flags_text = "\n".join(f"  ⚡ {f}" for f in legal_data["flags"]) or "  None detected"
    gdpr_text  = "\n".join(f"  ☐ {r}" for r in legal_data["gdpr_reqs"][:6])
    ip_text    = "\n".join(f"  ☐ {c}" for c in legal_data["ip_checklist"][:4])
    red_flags  = "\n".join(f"  🚨 {f}" for f in legal_data["contract_flags"][:4])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

DISCLAIMER: {legal_data['disclaimer']}

MISSION: Produce a {out_type} for jurisdiction: {jurisdiction}.

JURISDICTION NOTE: {juris_note}

RISK LEVEL DETECTED: {legal_data['risk_level'].upper()}

LEGAL FLAGS:
{flags_text}

GDPR REQUIREMENTS (top 6):
{gdpr_text}

IP CHECKLIST (top 4):
{ip_text}

CONTRACT RED FLAGS:
{red_flags}

TASK:
{task}

LEGAL CONTEXT:
{legal_ctx or "None provided"}

OUTPUT FORMAT:
## Legal Analysis: {out_type.replace('_',' ').title()} - {jurisdiction.upper()}

> ⚠️ {legal_data['disclaimer']}

### Risk Assessment
**Overall Risk Level:** {legal_data['risk_level'].upper()}
[2-sentence justification with specific legal basis]

### Key Legal Issues
[Numbered - each with: issue, relevant law/regulation, implication, recommended action]

### Compliance Checklist
[Each item: REQUIRED / RECOMMENDED / N/A - with brief explanation]

### Recommended Clauses / Provisions
[Specific language for contracts, policies, or notices - ready to use or adapt]

### What Needs a Real Lawyer
[Specific items that require qualified legal counsel - be direct]

### Next Action
[Single most important legal step]

RISK_LEVEL: {legal_data['risk_level']}
"""

_build_prompt = _build_legal_prompt  # spec alias


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


def legal_advisor_node(state: LegalAdvisorState) -> LegalAdvisorState:
    thread_id    = state.get("workflow_id", "local")
    task         = state.get("task", "").strip()
    out_type     = state.get("output_type", "general")
    jurisdiction = state.get("jurisdiction", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if jurisdiction not in VALID_JURISDICTIONS:
        raise ValueError(f"PERMANENT: jurisdiction '{jurisdiction}' not in {VALID_JURISDICTIONS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "jurisdiction": jurisdiction})
    legal_data = _detect_legal_risks(task, state.get("legal_context", ""))

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_legal_prompt(state, legal_data)

    try:
        advice = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    rl_match   = re.search(r'RISK_LEVEL:\s*(high|medium|low)', advice, re.IGNORECASE)
    risk_level = rl_match.group(1).lower() if rl_match else legal_data["risk_level"]

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "risk_level": risk_level})

    return {**state, "agent": ROLE, "legal_advice": advice, "risk_level": risk_level, "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(LegalAdvisorState)
    g.add_node("legal_advisor", legal_advisor_node)
    g.set_entry_point("legal_advisor")
    g.add_edge("legal_advisor", END)
    return g.compile()
