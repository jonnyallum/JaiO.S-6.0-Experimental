"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : ad_copy_writer
 SKILL : Ad Copy Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product (str), audience (str), platform (str),
               objective (str), usp (str — unique selling point),
               num_variants (int — default 3)
 Output keys : ad_variants (list[dict]), variant_count (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass generation.
 VARIANT_LIMIT = 6 (hard ceiling on variants per call).

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid platform/objective (ValueError), empty product
               or audience, num_variants > VARIANT_LIMIT
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: platform, objective, char limits
 POST — logged after success: variant_count, platforms confirmed

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

from typing import Any

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

ROLE = "ad_copy_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES    = 3
MAX_TOKENS     = 1800
VARIANT_LIMIT  = 6

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_PLATFORMS   = {"meta", "google", "linkedin", "twitter", "tiktok", "youtube"}
VALID_OBJECTIVES  = {"awareness", "traffic", "leads", "sales", "retargeting", "app_install"}

# ── Platform character limit specs ────────────────────────────────────────────
_PLATFORM_LIMITS: dict[str, dict] = {
    "meta": {
        "primary_text":  125,
        "headline":       40,
        "description":    30,
        "cta_options":    ["Shop Now", "Learn More", "Sign Up", "Get Quote", "Book Now"],
        "notes":          "Hook within first 3 lines (text truncates). Emoji OK.",
    },
    "google": {
        "headline":       30,   # up to 15 headlines, 30 chars each
        "description":    90,   # up to 4 descriptions, 90 chars each
        "display_url":    15,   # path fields
        "cta_options":    ["Buy Now", "Get Started", "Learn More", "Shop Today"],
        "notes":          "Include primary keyword in headline 1. No superlatives.",
    },
    "linkedin": {
        "introductory_text": 150,
        "headline":           70,
        "description":        100,
        "cta_options":        ["Learn More", "Sign Up", "Register", "Download", "Request Demo"],
        "notes":              "Professional tone. Lead with business value.",
    },
    "twitter": {
        "tweet_copy":    280,
        "card_headline":  70,
        "cta_options":   ["Learn More", "Shop Now", "Sign Up"],
        "notes":         "Hashtags: max 2. Conversational tone wins.",
    },
    "tiktok": {
        "ad_text":        100,
        "cta_options":    ["Shop Now", "Learn More", "Sign Up", "Download"],
        "notes":          "Hook in first second. Native-feel over polished.",
    },
    "youtube": {
        "headline":       15,   # skippable overlay
        "description":    70,
        "cta_options":    ["Learn More", "Shop Now", "Get Offer"],
        "notes":          "First 5s non-skippable — lead with problem/hook.",
    },
}

# ── Objective copy angle map ───────────────────────────────────────────────────
_OBJECTIVE_ANGLES: dict[str, str] = {
    "awareness":   "Brand story, mission, emotional resonance — no hard sell",
    "traffic":     "Curiosity gap, clear value prop, compelling CTA",
    "leads":       "Lead magnet, benefit-first, low-friction offer",
    "sales":       "Social proof, urgency/scarcity, risk reversal",
    "retargeting": "Objection handling, testimonials, limited-time incentive",
    "app_install": "Feature highlight, social proof, install hook",
}

# ── State ──────────────────────────────────────────────────────────────────────
class AdCopyState(BaseState):
    # Inputs
    product:      str   # product/service being advertised
    audience:     str   # target audience description
    platform:     str   # ad platform
    objective:    str   # campaign objective
    usp:          str   # unique selling point
    num_variants: int   # number of ad variants to generate
    thread_id:    str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    char_limits:   dict  # platform char limits (owner: this node)
    copy_angle:    str   # objective-derived angle (owner: this node)

    # Outputs
    ad_variants:   list  # list of variant dicts (owner: this node)
    variant_count: int   # confirmed count (owner: this node)
    error:         str   # failure reason if any (owner: this node)


# ── Phase 1 — pure limit lookup (no Claude) ───────────────────────────────────

def _get_platform_context(platform: str, objective: str) -> tuple[dict, str]:
    """
    Phase 1 — pure lookup. Returns (char_limits, copy_angle).
    No Claude, no I/O — independently testable.
    """
    limits = _PLATFORM_LIMITS[platform]
    angle  = _OBJECTIVE_ANGLES[objective]
    return limits, angle


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product: str,
    audience: str,
    platform: str,
    objective: str,
    usp: str,
    num_variants: int,
    char_limits: dict,
    copy_angle: str,
) -> str:
    """Pure function — assembles the ad copy brief from Phase 1 outputs."""
    persona    = get_persona(ROLE)
    limits_str = "\n".join(f"  {k}: {v}" for k, v in char_limits.items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} performance ad copywriter.

Platform    : {platform.upper()}
Objective   : {objective} — {copy_angle}
Product     : {product}
Audience    : {audience}
USP         : {usp}
Variants    : {num_variants}

Platform character limits & notes:
{limits_str}

Generate exactly {num_variants} ad variants. Format each as:

===VARIANT [N]===
HEADLINE: [text — within char limit]
BODY: [text — within char limit]
CTA: [one of the platform CTA options]
HOOK ANGLE: [one sentence — what psychological trigger this uses]
CHAR CHECK: HEADLINE=[count] BODY=[count]

Rules:
- Every headline MUST fit within the platform character limit — count carefully
- No generic phrases ("world-class", "best-in-class", "revolutionary")
- At least one variant must use social proof
- At least one variant must use urgency or scarcity
- Speak directly to the audience's pain or desire"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_copy(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _parse_variants(raw: str) -> list[dict]:
    """Extract structured variant dicts from Claude's formatted output."""
    variants = []
    blocks = raw.split("===VARIANT")
    for block in blocks[1:]:  # skip prefix
        variant: dict[str, str] = {}
        for field in ["HEADLINE", "BODY", "CTA", "HOOK ANGLE", "CHAR CHECK"]:
            m = __import__("re").search(rf"{field}:\s*(.+?)(?=\n[A-Z ]+:|$)", block, __import__("re").DOTALL)
            if m:
                variant[field.lower().replace(" ", "_")] = m.group(1).strip()
        if variant:
            variants.append(variant)
    return variants


# ── Node ───────────────────────────────────────────────────────────────────────

def ad_copy_writer_node(state: AdCopyState) -> AdCopyState:
    thread_id    = state.get("thread_id", "unknown")
    product      = state.get("product", "").strip()
    audience     = state.get("audience", "").strip()
    platform     = state.get("platform", "").lower().strip()
    objective    = state.get("objective", "").lower().strip()
    usp          = state.get("usp", "").strip()
    num_variants = int(state.get("num_variants", 3))

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product:
        return {**state, "error": "PERMANENT: product is required"}
    if not audience:
        return {**state, "error": "PERMANENT: audience is required"}
    if platform not in VALID_PLATFORMS:
        return {**state, "error": f"PERMANENT: platform '{platform}' not in {VALID_PLATFORMS}"}
    if objective not in VALID_OBJECTIVES:
        return {**state, "error": f"PERMANENT: objective '{objective}' not in {VALID_OBJECTIVES}"}
    if num_variants > VARIANT_LIMIT:
        return {**state, "error": f"PERMANENT: num_variants {num_variants} exceeds VARIANT_LIMIT={VARIANT_LIMIT}"}

    # ── Phase 1 — pure limit/angle lookup ────────────────────────────────────
    char_limits, copy_angle = _get_platform_context(platform, objective)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(product, audience, platform, objective, usp, num_variants, char_limits, copy_angle)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "platform": platform, "objective": objective,
        "num_variants": num_variants,
        "char_limit_keys": list(char_limits.keys()),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        raw = _write_copy(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Parse variants ────────────────────────────────────────────────────────
    ad_variants = _parse_variants(raw)
    if not ad_variants:
        # Fallback: return raw as single variant
        ad_variants = [{"raw": raw}]

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "variant_count": len(ad_variants),
        "platform": platform,
        "objective": objective,
    })

    return {
        **state,
        "ad_variants":   ad_variants,
        "variant_count": len(ad_variants),
        "char_limits":   char_limits,
        "copy_angle":    copy_angle,
        "error":         "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AdCopyState)
    g.add_node("ad_copy_writer", ad_copy_writer_node)
    g.set_entry_point("ad_copy_writer")
    g.add_edge("ad_copy_writer", END)
    return g.compile()
