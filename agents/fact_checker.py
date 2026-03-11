"""
Fact Checker — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : claim (str), supporting_context (str), output_type (VALID_OUTPUT_TYPES), domain (VALID_DOMAINS)
    Outputs: fact_check_report (str), verdict (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty claim, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty claim, unknown output_type/domain → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before claim analysis
    POST — fact_check_report + verdict persisted after successful generation
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

ROLE        = "fact_checker"
MAX_RETRIES = 3
MAX_TOKENS  = 2200

VALID_OUTPUT_TYPES = {
    "claim_verification", "source_audit", "statistic_check",
    "quote_verification", "product_claim_audit", "general",
}
VALID_DOMAINS = {
    "technology", "business", "science", "health", "finance",
    "marketing", "legal", "general",
}

# ── Claim Pattern Library ──────────────────────────────────────────────────────
_CLAIM_RED_FLAGS = [
    (r'\d+\s*%',                               "statistic",    "Percentage claim — requires source and methodology"),
    (r'(best|fastest|largest|most|only|first)', "superlative",  "Superlative — requires comparative evidence"),
    (r'(proven|guaranteed|certain|definitive)', "certainty",    "Certainty claim — science rarely uses these words"),
    (r'(always|never|all|none|every)',          "absolute",     "Absolute claim — almost always false or misleading"),
    (r'(experts say|studies show|research proves)', "vague_authority", "Vague authority — who? which study? when?"),
    (r'(recently|new study|new research)',      "time_vague",   "Temporal vagueness — when exactly?"),
    (r'"[^"]{10,}"',                                "quote",        "Quoted statement — verify exact wording and source"),
    (r'\$[\d,]+|\£[\d,]+|€[\d,]+',                "financial",    "Financial figure — verify currency, date, and source"),
]

_VERDICT_LEVELS = {
    "TRUE":             "Claim is accurate and well-supported by evidence",
    "MOSTLY_TRUE":      "Claim is largely accurate with minor caveats or missing context",
    "MIXED":            "Claim has elements of truth but is misleading or incomplete",
    "MOSTLY_FALSE":     "Claim is largely inaccurate — some kernel of truth exists",
    "FALSE":            "Claim is factually incorrect",
    "UNVERIFIABLE":     "Insufficient evidence to confirm or deny the claim",
    "OPINION":          "Claim is subjective — not a factual assertion",
}

_DOMAIN_STANDARDS = {
    "technology":  ["Check version/date — tech moves fast", "Distinguish marketing claims from benchmarks", "Verify compatibility claims"],
    "business":    ["Revenue claims need fiscal year and accounting standard", "Market size: TAM vs SAM vs SOM", "Growth rates: YoY vs MoM"],
    "science":     ["Peer-reviewed > preprint > press release", "Correlation ≠ causation", "Sample size and methodology matter"],
    "health":      ["RCT > observational study", "Relative risk vs absolute risk", "Regulatory approval status"],
    "finance":     ["Past performance ≠ future results (required disclosure)", "Verify exchange rates and dates", "GAAP vs non-GAAP metrics"],
    "marketing":   ["Survey methodology and sample size", "Award claims — verify awarding body", "Comparison terms — vs what?"],
    "legal":       ["Jurisdiction specific — law varies by country", "Court ruling ≠ settled law", "Verify case citation"],
    "general":     ["Primary source > secondary source", "Date of information matters", "Conflict of interest disclosure"],
}


class FactCheckerState(TypedDict, total=False):
    workflow_id:       str
    timestamp:         str
    agent:             str
    error:             str | None
    claim:             str
    supporting_context: str
    output_type:       str
    domain:            str
    fact_check_report: str
    verdict:           str


# ── Phase 1 — Claim Analysis (pure, no Claude) ────────────────────────────────
def _analyse_claim(claim: str, domain: str) -> dict:
    """Returns claim_data dict — pure regex and lookup, no Claude."""
    flags: list[tuple] = []
    for pattern, flag_type, description in _CLAIM_RED_FLAGS:
        matches = re.findall(pattern, claim, re.IGNORECASE)
        if matches:
            flags.append((flag_type, description, matches[:2]))

    domain_standards = _DOMAIN_STANDARDS.get(domain, _DOMAIN_STANDARDS["general"])
    complexity = "high" if len(flags) > 3 else "medium" if len(flags) > 1 else "low"

    return {
        "flags":            flags,
        "flag_count":       len(flags),
        "complexity":       complexity,
        "domain_standards": domain_standards,
        "verdict_levels":   _VERDICT_LEVELS,
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude Fact Check ────────────────────────────────────────────────
def _build_fact_check_prompt(state: FactCheckerState, claim_data: dict) -> str:
    persona  = get_persona(ROLE)
    claim    = state["claim"]
    ctx      = state.get("supporting_context", "")
    out_type = state.get("output_type", "claim_verification")
    domain   = state.get("domain", "general")

    flags_text = "
".join(
        f"  ⚠ [{ftype}] {desc} — found: {matches}"
        for ftype, desc, matches in claim_data["flags"]
    ) or "  No red-flag patterns detected"

    standards_text = "
".join(f"  • {s}" for s in claim_data["domain_standards"])
    verdicts_text  = "
".join(f"  {v}: {d}" for v, d in claim_data["verdict_levels"].items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Verify the claim below with rigorous, evidence-based analysis. Output type: {out_type}.

DOMAIN: {domain}
CLAIM COMPLEXITY: {claim_data['complexity'].upper()} ({claim_data['flag_count']} red-flag patterns)

CLAIM RED FLAGS DETECTED:
{flags_text}

DOMAIN STANDARDS:
{standards_text}

VERDICT SCALE:
{verdicts_text}

CLAIM TO VERIFY:
"""
{claim}
"""

SUPPORTING CONTEXT / EVIDENCE:
{ctx or "None provided — evaluate claim on its own merits and internal consistency"}

YOUR TASK:
1. Break the claim into individual verifiable sub-claims.
2. Evaluate each sub-claim against evidence, logic, and domain standards.
3. Identify what would be needed to fully verify each sub-claim.
4. Issue an overall verdict with confidence level.
5. Flag any misleading framing even if technically accurate.

OUTPUT FORMAT:
## Fact Check Report: {out_type.replace('_',' ').title()} — {domain}

### Claim Breakdown
| # | Sub-Claim | Verdict | Confidence | Evidence Required |
|---|---|---|---|---|
[rows]

### Evidence Analysis
[For each sub-claim: what supports it, what contradicts it, what is unknown]

### Misleading Framing (if any)
[Technically true statements that create false impressions]

### Sources Required for Full Verification
[Specific sources, databases, or experts that would confirm or deny]

### Overall Verdict
**Verdict:** [TRUE / MOSTLY_TRUE / MIXED / MOSTLY_FALSE / FALSE / UNVERIFIABLE / OPINION]
**Confidence:** [HIGH / MEDIUM / LOW]
**Justification:** [2–3 sentences]

### Recommended Revision (if needed)
[A more accurate version of the claim]

VERDICT: [TRUE/MOSTLY_TRUE/MIXED/MOSTLY_FALSE/FALSE/UNVERIFIABLE/OPINION]
"""

_build_prompt = _build_fact_check_prompt  # spec alias


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


def fact_checker_node(state: FactCheckerState) -> FactCheckerState:
    thread_id = state.get("workflow_id", "local")
    claim     = state.get("claim", "").strip()
    out_type  = state.get("output_type", "claim_verification")
    domain    = state.get("domain", "general")

    if not claim:
        raise ValueError("PERMANENT: claim is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if domain not in VALID_DOMAINS:
        raise ValueError(f"PERMANENT: domain '{domain}' not in {VALID_DOMAINS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "domain": domain})
    claim_data = _analyse_claim(claim, domain)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_fact_check_prompt(state, claim_data)

    try:
        report = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    verdict_match = re.search(r'VERDICT:\s*(TRUE|MOSTLY_TRUE|MIXED|MOSTLY_FALSE|FALSE|UNVERIFIABLE|OPINION)', report)
    verdict       = verdict_match.group(1) if verdict_match else "UNVERIFIABLE"

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "verdict": verdict})

    return {**state, "agent": ROLE, "fact_check_report": report, "verdict": verdict, "error": None}
