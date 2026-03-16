"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : competitor_monitor
SKILL : Competitor Monitor — scrape a public competitor URL, extract positioning signals,
        synthesise strategic intel report with gap analysis and opportunity map

Node Contract (@langraph doctrine):
  Inputs   : competitor_url (str), our_context (str), focus (str) — immutable after entry
  Outputs  : intel_report (str), error (str|None), agent (str)
  Tools    : requests [read-only scrape], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]
             Telemetry: CallMetrics per invocation — tokens, cost_usd, latency_ms [non-fatal]

Thread Memory (checkpoint-scoped):
  All CompetitorIntelState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors only).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.
  Scrape is attempted once — 403/blocked pages are PERMANENT failures, not retried.
  Re-scraping the same blocked URL produces the same result; retrying wastes budget.

Failure Discrimination:
  PERMANENT  → ValueError (url or our_context missing), HTTPError 403/404,
               ConnectionError (domain does not exist)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError (Claude only)
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (records scrape char count, focus)
  POST — Supabase log after completion (records intel report size)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone

import anthropic
import requests as http_requests
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

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE           = "competitor_monitor"
MAX_RETRIES    = 3
RETRY_MIN_S    = 3
RETRY_MAX_S    = 45
MAX_TOKENS     = 8000
SCRAPE_TIMEOUT = 10       # seconds — hard cap, never blocks indefinitely
RAW_CHARS      = 8000     # Raw HTML chars captured before cleaning
CLEAN_CHARS    = 5000     # Cleaned text chars passed to Claude
CONTEXT_CHARS  = 600      # Our context truncation

VALID_FOCUS = {"pricing", "content", "positioning", "technical", "general"}

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.5",
}


# ── State schema ─────────────────────────────────────────────────────────────────
class CompetitorIntelState(BaseState):
    # Inputs — written by caller, immutable inside this node
    competitor_url: str    # Public-facing competitor URL to scrape
    our_context: str       # Our product, positioning, target audience — for gap analysis
    focus: str             # pricing | content | positioning | technical | general
    # Outputs — written by this node, read by downstream nodes
    intel_report: str      # Structured intel with gaps and opportunities; empty on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Public page scrape (independently testable, no Claude) ───────────────
def _scrape_public_page(url: str) -> dict:
    """
    Fetch and clean a public competitor page. No auth. No JS rendering.
    Returns structured dict of extracted signals. PERMANENT failure on 403/404.
    Separation allows unit testing with mocked http_requests.get.
    """
    r = http_requests.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT)
    r.raise_for_status()  # 4xx/5xx → HTTPError → caller discriminates PERMANENT vs TRANSIENT

    html = r.text[:RAW_CHARS]

    title_m  = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title    = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "Not found"

    meta_m   = re.search(r"name=[\"']description[\"'][^>]+content=[\"']([^\"']*)", html, re.I)
    meta_desc = meta_m.group(1).strip() if meta_m else "Not found"

    h1s = [re.sub(r"<[^>]+>", "", h).strip()
           for h in re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)]
    h2s = [re.sub(r"<[^>]+>", "", h).strip()
           for h in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.I | re.S)]

    # Remove scripts and styles, then strip tags for clean text
    clean = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    clean = re.sub(r"<style[^>]*>.*?</style>", " ", clean, flags=re.S | re.I)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Pricing signal scan
    price_hits = re.findall(
        r"[£$€]\s?[\d,]+(?:\.\d{2})?|per month|/mo|/year|free tier|free plan|enterprise",
        html, re.I
    )

    # Nav/header text for site structure hint
    nav_m    = re.findall(r"<(?:nav|header)[^>]*>(.*?)</(?:nav|header)>", html, re.I | re.S)
    nav_text = re.sub(r"<[^>]+>", " ", " ".join(nav_m)).strip()

    return {
        "title":         title,
        "meta_desc":     meta_desc,
        "h1_tags":       h1s[:3],
        "h2_tags":       h2s[:6],
        "pricing_hints": list(set(price_hits))[:10],
        "nav_text":      nav_text[:500],
        "clean_content": clean[:CLEAN_CHARS],
        "http_status":   r.status_code,
    }


# ── Phase 2: Intel synthesis (Claude call, retried on transient errors only) ─────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _synthesise(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _build_prompt(url: str, data: dict, our_context: str, focus: str, persona: dict) -> str:
    """Format scraped data into a competitor intel prompt. Pure function — no I/O."""
    h1_text      = " | ".join(data["h1_tags"]) or "None found"
    h2_text      = " | ".join(data["h2_tags"]) or "None found"
    pricing_text = ", ".join(data["pricing_hints"]) or "No pricing signals found"

    return f"""{persona['personality']}

Produce a strategic competitor intelligence report. Reference actual copy and pricing
signals from the scraped data. Focus: {focus.upper()}. No filler. Max 500 words.

━━━ COMPETITOR: {url} ━━━
Title      : {data['title']}
Meta desc  : {data['meta_desc']}
H1 tags    : {h1_text}
H2 tags    : {h2_text}
Nav/Menu   : {data['nav_text']}
Price hints: {pricing_text}

━━━ PAGE CONTENT ━━━
{data['clean_content']}

━━━ OUR CONTEXT ━━━
{our_context[:CONTEXT_CHARS]}

━━━ REQUIRED OUTPUT ━━━
## Competitor Intel: {url}

### Positioning Summary
[Core claim, target audience, tone — specific to their actual copy]

### Pricing & Offer Structure
[Pricing model, tiers if visible, free trial / freemium signals]

### Content Strategy
[Topics they lead with, what they emphasise, content gaps vs our offering]

### Technical Signals
[Stack hints, tracking tools visible in source, page architecture]

### Our Gaps vs Them
[Where they outperform our current positioning — be specific, not generic]

### Our Opportunities
[What they miss that we can exploit — concrete angles with suggested copy direction]

### Recommended Actions
1. [Highest-value response to this intel]
2. [Second action]
3. [Third action]

### Verdict
[Threat level: LOW / MEDIUM / HIGH + primary opportunity in one paragraph]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def competitor_monitor_node(state: CompetitorIntelState) -> dict:
    """
    Competitor Monitor node — single pass, no loop.

    Execution order:
      1. Validate inputs (competitor_url, our_context required; focus validated)
      2. Scrape public page (Phase 1 — HTTP GET, PERMANENT on 403/404/ConnectionError)
      3. PRE checkpoint (before Claude call)
      4. Synthesise intel (Phase 2 — Claude)
      5. metrics.log() + metrics.persist() [non-fatal]
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: scrape is one-shot. Blocked pages are PERMANENT — retrying wastes budget.
    """
    thread_id      = state.get("workflow_id") or str(uuid.uuid4())
    competitor_url = state.get("competitor_url", "")
    focus          = state.get("focus", "general")
    persona        = get_persona(ROLE)
    notifier       = TelegramNotifier()
    state_logger   = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, url=competitor_url, focus=focus)

    try:
        if not competitor_url.strip():
            raise ValueError("competitor_url is required and cannot be empty.")
        if not state.get("our_context", "").strip():
            raise ValueError("our_context is required — describe our product and positioning.")
        if focus not in VALID_FOCUS:
            raise ValueError(
                f"Invalid focus '{focus}'. Must be one of: {', '.join(sorted(VALID_FOCUS))}"
            )

        claude  = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics = CallMetrics(thread_id, ROLE)

        # Phase 1 — scrape (PERMANENT on 403/404/ConnectionError)
        data = _scrape_public_page(competitor_url)
        log.info(f"{ROLE}.scraped", chars=len(data["clean_content"]),
                 pricing_hints=len(data["pricing_hints"]))

        # PRE checkpoint — mark synthesis started
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"url": competitor_url, "focus": focus, "status": "synthesising",
             "scrape_chars": len(data["clean_content"]),
             "pricing_hints": len(data["pricing_hints"])},
        )

        # Phase 2 — synthesise (TRANSIENT failures retried by tenacity)
        prompt       = _build_prompt(competitor_url, data, state.get("our_context", ""),
                                     focus, persona)
        intel_report = _synthesise(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"url": competitor_url, "focus": focus, "status": "completed",
             "report_chars": len(intel_report)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, report_chars=len(intel_report))
        return {"intel_report": intel_report, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry ─────────────────────────────────────────────
    except (ValueError, http_requests.exceptions.ConnectionError) as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="scrape_or_input",
                  error=msg, url=competitor_url)
        notifier.agent_error(ROLE, competitor_url, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"url": competitor_url, "status": "permanent_failure", "error": msg})
        return {"intel_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except http_requests.exceptions.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} scraping {competitor_url}"
        log.error(f"{ROLE}.scrape_blocked", failure_mode="http_error", error=msg)
        notifier.agent_error(ROLE, competitor_url, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"url": competitor_url, "status": "scrape_blocked", "error": msg})
        return {"intel_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, competitor_url, msg)
        return {"intel_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, competitor_url, msg)
        return {"intel_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(CompetitorIntelState)
    g.add_node("competitor_monitor", competitor_monitor_node)
    g.set_entry_point("competitor_monitor")
    g.add_edge("competitor_monitor", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
