"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : seo_specialist
SKILL : SEO Specialist — extract on-page signals, produce structured audit with schema
        recommendations, keyword gap analysis, and a prioritised 30-day action plan

Node Contract (@langraph doctrine):
  Inputs   : url (str), page_content (str), target_keywords (str),
             business_context (str), focus (str) — immutable after entry
  Outputs  : seo_report (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]
             Telemetry: CallMetrics per invocation — tokens, cost_usd, latency_ms [non-fatal]

Thread Memory (checkpoint-scoped):
  All SEOState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.
  Signal extraction (Phase 1) is deterministic — re-running produces identical output.

Failure Discrimination:
  PERMANENT  → ValueError (url or page_content missing/empty, invalid focus)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (records signals extracted, focus area)
  POST — Supabase log after completion (records report size, url)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import re
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
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE           = "seo_specialist"
MAX_RETRIES    = 3
RETRY_MIN_S    = 3
RETRY_MAX_S    = 45
MAX_TOKENS     = 1800
CONTENT_CHARS  = 5000
KEYWORD_CHARS  = 500
CONTEXT_CHARS  = 600
TITLE_SWEET    = (50, 60)
META_SWEET     = (140, 160)
MIN_WORDS      = 300
VALID_FOCUS    = {"technical", "content", "keywords", "schema", "general"}


class SEOState(BaseState):
    url: str
    page_content: str
    target_keywords: str
    business_context: str
    focus: str
    seo_report: str


# ── Phase 1 — signal extraction & issue flagging (pure, no Claude) ─────────────────

def _extract_signals(content: str) -> dict:
    text = content[:CONTENT_CHARS]
    title_m   = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title     = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "Not found"
    meta_m    = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', text, re.I)
    meta_desc = meta_m.group(1).strip() if meta_m else "Not found"
    h1s = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<h1[^>]*>(.*?)</h1>", text, re.I | re.S)]
    h2s = re.findall(r"<h2[^>]*>(.*?)</h2>", text, re.I | re.S)
    has_ld    = "application/ld+json" in text
    can_m     = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']*)', text, re.I)
    canonical = can_m.group(1) if can_m else "Not found"
    og_m      = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)', text, re.I)
    og_title  = og_m.group(1) if og_m else ""
    imgs      = re.findall(r"<img[^>]+>", text, re.I)
    no_alt    = sum(1 for i in imgs if "alt=" not in i.lower())
    clean     = re.sub(r"<[^>]+>", " ", text)
    words     = len(clean.split())
    links     = re.findall(r'href=["\']([^"\']*)', text, re.I)
    internal  = sum(1 for l in links if not l.startswith("http"))
    return {
        "title": title, "title_len": len(title),
        "meta_desc": meta_desc, "meta_desc_len": len(meta_desc),
        "h1_tags": h1s[:3], "h2_count": len(h2s),
        "word_count": words, "has_schema_ld": has_ld,
        "canonical": canonical, "og_title": og_title,
        "images_total": len(imgs), "images_no_alt": no_alt,
        "internal_links": internal, "external_links": len(links) - internal,
    }


def _flag_issues(s: dict) -> list:
    issues = []
    if s["title"] == "Not found": issues.append("CRITICAL: No <title> tag")
    elif s["title_len"] < TITLE_SWEET[0]: issues.append(f"HIGH: Title too short ({s['title_len']} chars)")
    elif s["title_len"] > TITLE_SWEET[1]: issues.append(f"MEDIUM: Title may truncate ({s['title_len']} chars)")
    if s["meta_desc"] == "Not found": issues.append("HIGH: No meta description")
    elif s["meta_desc_len"] > META_SWEET[1]: issues.append(f"MEDIUM: Meta too long ({s['meta_desc_len']} chars)")
    if not s["h1_tags"]: issues.append("HIGH: No H1 tag")
    elif len(s["h1_tags"]) > 1: issues.append(f"MEDIUM: Multiple H1 tags ({len(s['h1_tags'])})")
    if s["word_count"] < MIN_WORDS: issues.append(f"HIGH: Thin content ({s['word_count']} words)")
    if not s["has_schema_ld"]: issues.append("MEDIUM: No JSON-LD schema markup")
    if s["canonical"] == "Not found": issues.append("MEDIUM: No canonical tag")
    if s["images_no_alt"] > 0: issues.append(f"MEDIUM: {s['images_no_alt']} images missing alt text")
    return issues


# ── Phase 2 — Claude call (TRANSIENT errors retried) ────────────────────────────────
def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
       retry=retry_if_exception_type((anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)),
       reraise=True)
def _audit(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    metrics.start()
    response = client.messages.create(model="claude-sonnet-4-6", max_tokens=MAX_TOKENS,
                                      messages=[{"role": "user", "content": prompt}])
    metrics.record(response)
    return response.content[0].text
_generate = _audit  # spec alias



def _build_prompt(url, content, signals, issues, keywords, context, focus, persona) -> str:
    h1_text  = ", ".join(f'"{h}"' for h in signals["h1_tags"]) or "None found"
    issue_md = "\n".join(f"  - {i}" for i in issues) or "  None auto-detected"
    return f"""{persona['personality']}

Perform a detailed SEO audit. Reference actual tag content. Write recommended tags verbatim.

━━━ PAGE ━━━
URL              : {url}
Business context : {context[:CONTEXT_CHARS]}
Target keywords  : {keywords[:KEYWORD_CHARS]}
Focus area       : {focus.upper()}

━━━ SIGNALS ━━━
Title       : {signals['title']} ({signals['title_len']} chars)
Meta desc   : {signals['meta_desc']} ({signals['meta_desc_len']} chars)
H1          : {h1_text}
H2 count    : {signals['h2_count']}
Words       : {signals['word_count']}
Schema JSON-LD: {"Yes" if signals['has_schema_ld'] else "MISSING"}
Canonical   : {signals['canonical']}
OG title    : {signals['og_title'] or "Missing"}
Images w/o alt: {signals['images_no_alt']} of {signals['images_total']}

━━━ FLAGGED ISSUES ━━━
{issue_md}

━━━ CONTENT SAMPLE ━━━
{content[:2000]}

━━━ OUTPUT FORMAT ━━━
## SEO Audit: {url}
**Overall Score:** X/10

### Recommended Tags (copy-paste ready)
**Title:** [50-60 char, primary keyword near front]
**Meta Description:** [140-160 char with CTA]

### Keyword Gap Analysis
| Keyword | In Title | In H1 | In Meta | In Body | Priority |
|---|---|---|---|---|---|

### Content Assessment
[Word count, readability, internal linking, thin content risk]

### Technical Issues
[Canonical, schema, OG, crawlability — specific fixes]

### Schema Recommendation
[Schema type + complete JSON-LD snippet]

### 30-Day Action Plan
| Priority | Task | Impact | Time |
|---|---|---|---|

### Verdict
[2 sentences — current state + single most impactful fix]"""


def seo_specialist_node(state: SEOState) -> dict:
    thread_id = state.get("workflow_id") or str(uuid.uuid4())
    url = state.get("url", ""); focus = state.get("focus", "general")
    persona = get_persona(ROLE); notifier = TelegramNotifier()
    state_logger = SupabaseStateLogger()
    def _checkpoint(cid, payload): state_logger.log_state(thread_id, cid, ROLE, payload)
    log.info(f"{ROLE}.started", thread_id=thread_id, url=url, focus=focus)
    try:
        if not url.strip(): raise ValueError("url is required.")
        if not state.get("page_content", "").strip(): raise ValueError("page_content is required.")
        if focus not in VALID_FOCUS:
            raise ValueError(f"Invalid focus '{focus}'. Must be one of: {', '.join(sorted(VALID_FOCUS))}")
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics = CallMetrics(thread_id, ROLE)
        signals = _extract_signals(state["page_content"])
        issues  = _flag_issues(signals)
        _checkpoint(f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"url": url, "focus": focus, "status": "auditing",
                     "word_count": signals["word_count"], "issues": len(issues)})
        prompt     = _build_prompt(url, state["page_content"], signals, issues,
                                   state.get("target_keywords",""), state.get("business_context",""),
                                   focus, persona)
        seo_report = _audit(claude, prompt, metrics)
        metrics.log(); metrics.persist()
        _checkpoint(f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"url": url, "status": "completed", "report_chars": len(seo_report)})
        log.info(f"{ROLE}.completed", thread_id=thread_id, report_chars=len(seo_report))
        return {"seo_report": seo_report, "error": None, "workflow_id": thread_id, "agent": ROLE}
    except ValueError as exc:
        msg = str(exc); log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, url, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"url": url, "status": "permanent_failure", "error": msg})
        return {"seo_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}
    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"; notifier.agent_error(ROLE, url, msg)
        return {"seo_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"; log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, url, msg)
        return {"seo_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(SEOState)
    g.add_node("seo_specialist", seo_specialist_node)
    g.set_entry_point("seo_specialist")
    g.add_edge("seo_specialist", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
