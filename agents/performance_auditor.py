"""
Performance Auditor — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), perf_context (str), output_type (VALID_OUTPUT_TYPES), target_platform (VALID_PLATFORMS)
    Outputs: perf_report (str), score_summary (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/target_platform → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before perf heuristic analysis
    POST — perf_report + score_summary persisted after successful generation
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

ROLE        = "performance_auditor"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_OUTPUT_TYPES = {
    "lighthouse_audit", "bundle_analysis", "mobile_qa", "core_web_vitals",
    "api_latency_review", "db_query_audit", "memory_profile", "general",
}
VALID_PLATFORMS = {
    "web_nextjs", "web_react", "mobile_expo", "api_node", "api_python",
    "supabase_db", "general",
}

# ── Performance Thresholds ─────────────────────────────────────────────────────
_CORE_WEB_VITALS = {
    "LCP":  {"good": 2500,  "needs_improvement": 4000,  "unit": "ms",  "label": "Largest Contentful Paint"},
    "INP":  {"good": 200,   "needs_improvement": 500,   "unit": "ms",  "label": "Interaction to Next Paint"},
    "CLS":  {"good": 0.1,   "needs_improvement": 0.25,  "unit": "",    "label": "Cumulative Layout Shift"},
    "TTFB": {"good": 800,   "needs_improvement": 1800,  "unit": "ms",  "label": "Time to First Byte"},
    "FCP":  {"good": 1800,  "needs_improvement": 3000,  "unit": "ms",  "label": "First Contentful Paint"},
}

_PLATFORM_CHECKS = {
    "web_nextjs": [
        "Image optimisation: next/image with width/height + priority on hero",
        "Font optimisation: next/font — no layout shift from web fonts",
        "Bundle splitting: dynamic imports for heavy components",
        "ISR / SSG where possible — reduce server compute",
        "Edge runtime for lightweight API routes",
        "No unused CSS in production (Tailwind purge active)",
        "Streaming with Suspense for slow data fetches",
    ],
    "mobile_expo": [
        "FlatList with getItemLayout for long lists",
        "Image caching: expo-image (not stock Image)",
        "Hermes engine enabled",
        "Bundle size: npx expo export --dump-assetmap",
        "No synchronous storage reads on main thread",
        "Reanimated for animations — not Animated API",
    ],
    "api_node": [
        "Connection pooling configured (pg-pool / Prisma pool)",
        "Response compression (gzip/brotli)",
        "Cache-Control headers on immutable assets",
        "Avoid await in loops — use Promise.all()",
        "Rate limiting per IP (express-rate-limit / upstash)",
        "Streaming large responses — no buffering",
    ],
    "supabase_db": [
        "EXPLAIN ANALYZE on slow queries",
        "Indexes on all FK columns + frequent WHERE columns",
        "Avoid SELECT * — explicit column lists",
        "Use connection pooling (pgBouncer via Supabase)",
        "Batch inserts with unnest() not row-by-row",
        "Materialised views for complex aggregations",
    ],
    "general": [
        "Profile before optimising — measure, don't guess",
        "Largest bottleneck first — 80/20 rule",
        "Cache aggressively at every layer",
        "Lazy-load below-the-fold content",
        "Compress all assets",
    ],
}

_ANTI_PATTERNS = [
    ("Unoptimised images",          r'<img\s',                    "Replace with next/image or expo-image"),
    ("Blocking script tags",        r'<script\s+src=',            "Add async/defer or move to module"),
    ("SELECT *",                    r'SELECT\s+\*\s+FROM',        "Explicit column list — fetch only what you need"),
    ("await in loop",               r'for\s*\(.*\)\s*\{[^}]*await', "Replace with Promise.all()"),
    ("console.log in production",   r'console\.log\(',            "Remove or gate behind NODE_ENV check"),
]


class PerformanceAuditorState(TypedDict, total=False):
    workflow_id:     str
    timestamp:       str
    agent:           str
    error:           str | None
    task:            str
    perf_context:    str
    output_type:     str
    target_platform: str
    perf_report:     str
    score_summary:   str


# ── Phase 1 — Heuristic Analysis (pure, no Claude) ────────────────────────────
def _analyse_perf_signals(task: str, perf_context: str, platform: str) -> dict:
    """Returns perf_data dict — pure heuristics and lookups."""
    combined    = (task + " " + perf_context).lower()
    checks      = _PLATFORM_CHECKS.get(platform, _PLATFORM_CHECKS["general"])
    anti_hits: list[str] = []
    for label, pattern, fix in _ANTI_PATTERNS:
        if re.search(pattern, perf_context, re.IGNORECASE):
            anti_hits.append(f"{label} → {fix}")
    flags: list[str] = []
    if "slow" in combined or "latency" in combined:
        flags.append("Latency complaint — profile DB queries and API round-trips first")
    if "lighthouse" in combined or "score" in combined:
        flags.append("Lighthouse target — focus on LCP, INP, CLS in that order")
    if "bundle" in combined or "size" in combined:
        flags.append("Bundle bloat — run bundle analyser, find largest culprits")
    if "mobile" in combined:
        flags.append("Mobile-first — test on mid-range Android, not just iPhone")
    return {
        "platform_checks": checks,
        "anti_patterns":   anti_hits,
        "flags":           flags,
        "cwv":             _CORE_WEB_VITALS,
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude Perf Report ───────────────────────────────────────────────
def _build_perf_prompt(state: PerformanceAuditorState, perf_data: dict) -> str:
    persona   = get_persona(ROLE)
    task      = state["task"]
    ctx       = state.get("perf_context", "")
    out_type  = state.get("output_type", "general")
    platform  = state.get("target_platform", "general")

    checks_text = "
".join(f"  ☐ {c}" for c in perf_data["platform_checks"])
    flags_text  = "
".join(f"  ⚡ {f}" for f in perf_data["flags"]) or "  None detected"
    anti_text   = "
".join(f"  ✗ {a}" for a in perf_data["anti_patterns"]) or "  None detected in context"
    cwv_text    = "
".join(
        f"  {k}: good <{v['good']}{v['unit']} | needs work <{v['needs_improvement']}{v['unit']} ({v['label']})"
        for k, v in perf_data["cwv"].items()
    )

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Deliver a production-grade {out_type} for platform: {platform}.

CORE WEB VITALS THRESHOLDS:
{cwv_text}

PLATFORM CHECKLIST:
{checks_text}

ANTI-PATTERNS DETECTED IN CONTEXT:
{anti_text}

PERFORMANCE FLAGS:
{flags_text}

TASK:
{task}

CONTEXT / CODE SNIPPET:
{ctx[:3000] or "None provided"}

OUTPUT FORMAT:
## Performance Audit: {out_type.replace('_',' ').title()} — {platform}

### Score Summary
| Metric | Current | Target | Status |
|---|---|---|---|
[Estimated or measured values]

### Critical Issues (fix first)
[Numbered — each with: problem, impact, exact fix, effort estimate]

### Platform Checklist Review
[Each item: PASS / FAIL / UNKNOWN — with evidence]

### Anti-Pattern Fixes
[Each detected anti-pattern with code-level fix]

### Quick Wins (< 30 min each)
[5 changes that immediately move the needle]

### Monitoring Setup
[What to measure ongoing — tools, thresholds, alerts]

### Next Action
[Single highest-ROI step]
"""

_build_prompt = _build_perf_prompt  # spec alias


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


def performance_auditor_node(state: PerformanceAuditorState) -> PerformanceAuditorState:
    thread_id = state.get("workflow_id", "local")
    task      = state.get("task", "").strip()
    out_type  = state.get("output_type", "general")
    platform  = state.get("target_platform", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"PERMANENT: target_platform '{platform}' not in {VALID_PLATFORMS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "platform": platform})
    perf_data = _analyse_perf_signals(task, state.get("perf_context", ""), platform)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_perf_prompt(state, perf_data)

    try:
        report = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    score_match   = re.search(r'### Score Summary([\s\S]+?)(?=###|$)', report)
    score_summary = score_match.group(1).strip() if score_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "platform": platform})

    return {**state, "agent": ROLE, "perf_report": report, "score_summary": score_summary, "error": None}
