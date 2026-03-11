"""
Copywriter - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), brand_context (str), output_type (VALID_OUTPUT_TYPES), copy_format (VALID_COPY_FORMATS)
    Outputs: copy_output (str), headline (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/copy_format → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before copy analysis
    POST - copy_output + headline persisted after successful generation
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

ROLE        = "copywriter"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "headline_variants", "landing_page_copy", "email_copy", "ui_microcopy",
    "ad_copy", "onboarding_flow", "error_messages", "brand_tagline", "general",
}
VALID_COPY_FORMATS = {
    "short_form", "long_form", "microcopy", "conversational",
    "direct_response", "brand_storytelling", "general",
}

# ── Copy Frameworks ────────────────────────────────────────────────────────────
_COPY_FORMULAS = {
    "headline":       "Specific Outcome + Time Frame + Objection Handled",
    "cta":            "Verb + Benefit (not 'Submit' - 'Get my free report')",
    "value_prop":     "We help [WHO] achieve [OUTCOME] without [PAIN]",
    "email_subject":  "Curiosity gap OR self-interest OR urgency - never all three",
    "error_message":  "What happened + Why + What to do next (3-part formula)",
    "onboarding":     "Welcome → First win → Next step (never 'explore the app')",
}

_TONE_GUIDES = {
    "short_form":          "Punchy. One idea per line. White space is your friend.",
    "long_form":           "Build tension → sustain interest → resolve with CTA.",
    "microcopy":           "Invisible when right, painful when wrong. 5 words max per label.",
    "conversational":      "Write like you talk. Contractions always. Never corporate.",
    "direct_response":     "Every word earns its place. Benefit first. Features second. Always.",
    "brand_storytelling":  "Conflict → stakes → resolution. The brand is the guide, not the hero.",
    "general":             "Clear > clever. Specific > vague. Active > passive.",
}

_POWER_WORDS = {
    "urgency":    ["now", "today", "instantly", "immediately", "deadline"],
    "exclusivity":["exclusive", "private", "members only", "invitation", "limited"],
    "proof":      ["proven", "verified", "tested", "trusted by", "results"],
    "ease":       ["simple", "effortless", "in minutes", "one click", "no expertise"],
    "gain":       ["increase", "grow", "unlock", "discover", "maximise"],
    "loss":       ["avoid", "protect", "prevent", "stop losing", "never again"],
}

_COPY_KILLERS = [
    ("Passive voice",          r'(is being|was being|have been|will be)',  "Use active voice"),
    ("Weasel words",           r'(some|many|often|usually|typically|may)', "Be specific or cut it"),
    ("Feature not benefit",    r'(features?|functionality|capability)',    "Lead with the outcome, not the feature"),
    ("Weak CTA",               r'(click here|learn more|submit|read more)',"Use action + benefit CTAs"),
    ("Corporate fluff",        r'(leverage|synergy|solutions|cutting-edge)',"Replace with plain language"),
]


class CopywriterState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    brand_context: str
    output_type:   str
    copy_format:   str
    copy_output:   str
    headline:      str


# ── Phase 1 - Copy Analysis (pure, no Claude) ─────────────────────────────────
def _analyse_copy_brief(task: str, copy_format: str) -> dict:
    """Returns copy_data dict - pure lookup and heuristics."""
    tone       = _TONE_GUIDES.get(copy_format, _TONE_GUIDES["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if "headline" in task_lower:
        flags.append(f"Headline formula: {_COPY_FORMULAS['headline']}")
    if "cta" in task_lower or "button" in task_lower:
        flags.append(f"CTA formula: {_COPY_FORMULAS['cta']}")
    if "email" in task_lower:
        flags.append(f"Subject line rule: {_COPY_FORMULAS['email_subject']}")
    if "error" in task_lower or "empty state" in task_lower:
        flags.append(f"Error/empty copy: {_COPY_FORMULAS['error_message']}")
    if "onboard" in task_lower:
        flags.append(f"Onboarding arc: {_COPY_FORMULAS['onboarding']}")

    return {
        "tone":        tone,
        "formulas":    _COPY_FORMULAS,
        "power_words": _POWER_WORDS,
        "copy_killers": _COPY_KILLERS,
        "flags":       flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Copy ──────────────────────────────────────────────────────
def _build_copy_prompt(state: CopywriterState, copy_data: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    brand_ctx   = state.get("brand_context", "")
    out_type    = state.get("output_type", "general")
    copy_format = state.get("copy_format", "general")

    flags_text  = "\n".join(f"  ⚡ {f}" for f in copy_data["flags"]) or "  None detected"
    killers_txt = "\n".join(f"  ✗ {label}: {fix}" for label, _, fix in copy_data["copy_killers"])
    power_txt   = " | ".join(f"{k}: {', '.join(v[:3])}" for k, v in copy_data["power_words"].items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Write conversion-grade {out_type} in {copy_format} format.

TONE GUIDE: {copy_data['tone']}

COPY KILLERS TO AVOID:
{killers_txt}

POWER WORD CATEGORIES:
{power_txt}

COPY FLAGS:
{flags_text}

TASK:
{task}

BRAND CONTEXT:
{brand_ctx or "None provided - infer voice from task and use clean, direct defaults"}

OUTPUT FORMAT:
## Copy: {out_type.replace('_',' ').title()} - {copy_format}

### Headline Variants (5 options)
1. [Option A - curiosity gap]
2. [Option B - direct benefit]
3. [Option C - social proof angle]
4. [Option D - urgency/loss]
5. [Option E - bold provocation]

### Primary Copy
[Full copy output - no placeholders, no [INSERT NAME], production ready]

### Microcopy Variants
[CTAs, button labels, helper text, error messages - if applicable]

### Copy Notes
[Tone decisions, word choices, what was deliberately cut and why]

### A/B Test Suggestion
[One specific element to split-test with hypothesis]

HEADLINE: [Best single headline from the 5 above]
"""

_build_prompt = _build_copy_prompt  # spec alias


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


def copywriter_node(state: CopywriterState) -> CopywriterState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    out_type    = state.get("output_type", "general")
    copy_format = state.get("copy_format", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if copy_format not in VALID_COPY_FORMATS:
        raise ValueError(f"PERMANENT: copy_format '{copy_format}' not in {VALID_COPY_FORMATS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "copy_format": copy_format})
    copy_data = _analyse_copy_brief(task, copy_format)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_copy_prompt(state, copy_data)

    try:
        output = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    hl_match = re.search(r'HEADLINE:\s*(.+)', output)
    headline = hl_match.group(1).strip() if hl_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "copy_format": copy_format})

    return {**state, "agent": ROLE, "copy_output": output, "headline": headline, "error": None}
