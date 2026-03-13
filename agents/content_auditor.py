"""
Content Depth Auditor - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : content (str), content_type (VALID_CONTENT_TYPES), audit_focus (VALID_AUDIT_FOCUSES)
    Outputs: audit_report (str), depth_score (int), fluff_count (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty content, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty content, unknown content_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before fluff scan
    POST - audit_report + depth_score persisted after successful generation
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

ROLE        = "content_auditor"
MAX_RETRIES = 3
MAX_TOKENS  = 2200

VALID_CONTENT_TYPES = {
    "blog_post", "landing_page", "email", "social_post", "case_study",
    "whitepaper", "ad_copy", "video_script", "general",
}
VALID_AUDIT_FOCUSES = {"depth", "truth_lock", "fluff_removal", "storytelling", "all"}

# ── Fluff Patterns ────────────────────────────────────────────────────────────
_FLUFF_PATTERNS = [
    r"in today\'s (fast-paced|digital|modern|ever-changing) world",
    r'(game-?changer|paradigm shift|synergy|leverage|circle back|bandwidth)',
    r'(it goes without saying|needless to say|as we all know)',
    r'(very|extremely|incredibly|absolutely|totally)\s+\w+',
    r'(utilize|utilisation)',  # just say "use"
    r'(deep dive|unpack|double-click on|drill down)',
    r'(seamless(ly)?|frictionless(ly)?|robust|scalable)(?!\s+\w+\s+that)',
    r'(comprehensive|holistic|end-to-end|best-in-class)',
    r'(transformative|revolutionary|cutting-edge|state-of-the-art)',
    r'(at the end of the day|when all is said and done|the bottom line is)',
]

_DEPTH_BENCHMARKS = {
    "blog_post":    {"min_words": 800,  "required": ["hook", "evidence", "takeaway"]},
    "landing_page": {"min_words": 300,  "required": ["headline", "benefit", "cta"]},
    "email":        {"min_words": 100,  "required": ["subject_hook", "body", "cta"]},
    "social_post":  {"min_words": 20,   "required": ["hook", "value", "engagement"]},
    "case_study":   {"min_words": 1200, "required": ["problem", "solution", "result"]},
    "whitepaper":   {"min_words": 2000, "required": ["abstract", "evidence", "conclusion"]},
    "ad_copy":      {"min_words": 20,   "required": ["hook", "benefit", "cta"]},
    "video_script": {"min_words": 200,  "required": ["hook_first_5s", "value", "cta"]},
    "general":      {"min_words": 100,  "required": []},
}


class ContentAuditorState(TypedDict, total=False):
    workflow_id:  str
    timestamp:    str
    agent:        str
    error:        str | None
    content:      str
    content_type: str
    audit_focus:  str
    audit_report: str
    depth_score:  int
    fluff_count:  int


# ── Phase 1 - Fluff Scan (pure, no Claude) ────────────────────────────────────
def _scan_for_fluff(content: str) -> tuple[int, list[str], int]:
    """Returns (fluff_count, examples, depth_score_0_10)."""
    examples: list[str] = []
    for pattern in _FLUFF_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        examples.extend(matches[:2])
    fluff_count = len(examples)
    word_count  = len(content.split())
    # depth score: starts at 10, loses points for fluff density and short length
    fluff_density = fluff_count / max(word_count / 100, 1)
    depth_score   = max(0, min(10, round(10 - fluff_density * 2 - (0 if word_count > 300 else 3))))
    return fluff_count, examples[:10], depth_score

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Audit ─────────────────────────────────────────────────────
def _build_audit_prompt(state: ContentAuditorState, fluff_count: int, examples: list, depth_score: int) -> str:
    persona      = get_persona(ROLE)
    content      = state["content"]
    content_type = state.get("content_type", "general")
    audit_focus  = state.get("audit_focus", "all")
    benchmark    = _DEPTH_BENCHMARKS.get(content_type, _DEPTH_BENCHMARKS["general"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Audit the content below for depth, truth, and zero fluff.

CONTENT TYPE: {content_type}
AUDIT FOCUS: {audit_focus}
DEPTH BENCHMARK: min {benchmark['min_words']} words | required elements: {benchmark['required']}

REGEX PRE-SCAN:
  Fluff count: {fluff_count}
  Depth score (0–10): {depth_score}
  Fluff examples found: {examples}

CONTENT:
'''
{content[:5000]}
'''

YOUR TASK:
1. Identify every piece of fluff, vague claim, or filler - quote it exactly.
2. Check for required depth elements: {benchmark['required']}
3. Assess storytelling quality (hook, tension, resolution).
4. Provide a rewritten version of the weakest paragraph - zero fluff, maximum punch.
5. Give an overall verdict.

OUTPUT FORMAT:
## Content Depth Audit
**Content Type:** {content_type}
**Audit Focus:** {audit_focus}
**Word Count:** [actual count]
**Depth Score:** [0–10]

### Fluff Violations (quote each, with fix)
[Numbered list: original → suggested replacement]

### Missing Depth Elements
[Which required elements are absent or underdeveloped]

### Storytelling Assessment
[Hook strength, tension, resolution - 2–3 sentences each]

### Rewritten Sample
[One weak paragraph, rewritten to full depth - no fluff]

### Verdict
[STRONG - publish ready | NEEDS WORK - fix listed issues | REJECT - rewrite from scratch]

DEPTH_SCORE: [0-10]
"""

_build_prompt = _build_audit_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code in (429, 529)
    return False


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _audit(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    metrics.log()
    metrics.persist()
    return response.content[0].text


def _extract_depth_score(report: str) -> int:
    m = re.search(r'DEPTH_SCORE:\s*(\d+)', report)
    return int(m.group(1)) if m else 0


def content_auditor_node(state: ContentAuditorState) -> ContentAuditorState:
    thread_id    = state.get("workflow_id", "local")
    content      = state.get("content", "").strip()
    content_type = state.get("content_type", "general")
    audit_focus  = state.get("audit_focus", "all")

    if not content:
        raise ValueError("PERMANENT: content is required.")
    if content_type not in VALID_CONTENT_TYPES:
        raise ValueError(f"PERMANENT: content_type '{content_type}' not in {VALID_CONTENT_TYPES}")
    if audit_focus not in VALID_AUDIT_FOCUSES:
        raise ValueError(f"PERMANENT: audit_focus '{audit_focus}' not in {VALID_AUDIT_FOCUSES}")

    checkpoint("PRE", thread_id, ROLE, {"content_type": content_type, "audit_focus": audit_focus})

    fluff_count, examples, depth_score = _scan_for_fluff(content)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_audit_prompt(state, fluff_count, examples, depth_score)

    try:
        report = _audit(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    final_score = _extract_depth_score(report) or depth_score

    checkpoint("POST", thread_id, ROLE, {"depth_score": final_score, "fluff_count": fluff_count})

    return {
        **state,
        "agent":        ROLE,
        "audit_report": report,
        "depth_score":  final_score,
        "fluff_count":  fluff_count,
        "error":        None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(ContentAuditorState)
    g.add_node("content_auditor", content_auditor_node)
    g.set_entry_point("content_auditor")
    g.add_edge("content_auditor", END)
    return g.compile()
