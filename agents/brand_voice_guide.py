"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 brand_voice_guide — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : brand_name (str), industry (str),
               audience (str), tone_keywords (str — 3–6 adjectives),
               brand_values (str — optional),
               sample_content (str — optional existing brand copy),
               output_type (str)
 Output keys : voice_guide (str), voice_spectrum (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 voice spectrum mapping →
 Phase 2 Claude guide. TONE_KEYWORD_LIMIT = 8.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid industry/output_type (ValueError),
               empty brand_name, audience, or tone_keywords,
               more than TONE_KEYWORD_LIMIT tone keywords
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: industry, output_type, keyword_count,
        has_sample_content
 POST — logged after success: voice_guide char count, spectrum keys

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

import anthropic
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona

# ── Identity ──────────────────────────────────────────────────────────────────
ROLE = "brand_voice_guide"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES        = 3
MAX_TOKENS         = 2600
TONE_KEYWORD_LIMIT = 8

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_INDUSTRIES = {
    "tech", "saas", "ecommerce", "fashion", "food_beverage", "finance",
    "healthcare", "education", "agency", "consultancy", "nonprofit", "general"
}
VALID_OUTPUT_TYPES = {
    "full_guide", "voice_spectrum_only", "do_dont_table",
    "messaging_hierarchy", "channel_adaptation_guide", "rewrite_samples"
}

# ── Tone spectrum poles — for mapping keywords to positions ────────────────────
_SPECTRUM_POLES: dict[str, tuple[str, str]] = {
    "formality":    ("Casual / conversational",   "Formal / authoritative"),
    "warmth":       ("Cool / distant",             "Warm / personal"),
    "energy":       ("Calm / measured",            "Energetic / punchy"),
    "complexity":   ("Simple / plain language",    "Sophisticated / nuanced"),
    "humour":       ("Serious / earnest",          "Playful / witty"),
    "directness":   ("Indirect / gentle",          "Direct / bold"),
}

# ── Tone keyword → spectrum position map ──────────────────────────────────────
_KEYWORD_SIGNALS: dict[str, dict[str, float]] = {
    # Format: keyword → {dimension: position 0.0 (pole A) to 1.0 (pole B)}
    "professional":  {"formality": 0.75, "warmth": 0.4, "energy": 0.3, "complexity": 0.6, "humour": 0.1, "directness": 0.6},
    "friendly":      {"formality": 0.25, "warmth": 0.8, "energy": 0.6, "complexity": 0.3, "humour": 0.5, "directness": 0.5},
    "bold":          {"formality": 0.4,  "warmth": 0.4, "energy": 0.9, "complexity": 0.4, "humour": 0.3, "directness": 0.9},
    "expert":        {"formality": 0.7,  "warmth": 0.4, "energy": 0.4, "complexity": 0.8, "humour": 0.1, "directness": 0.7},
    "witty":         {"formality": 0.2,  "warmth": 0.6, "energy": 0.7, "complexity": 0.5, "humour": 0.9, "directness": 0.6},
    "warm":          {"formality": 0.2,  "warmth": 0.9, "energy": 0.5, "complexity": 0.3, "humour": 0.4, "directness": 0.4},
    "direct":        {"formality": 0.5,  "warmth": 0.3, "energy": 0.7, "complexity": 0.4, "humour": 0.2, "directness": 0.95},
    "playful":       {"formality": 0.1,  "warmth": 0.7, "energy": 0.8, "complexity": 0.2, "humour": 0.85,"directness": 0.5},
    "authoritative": {"formality": 0.9,  "warmth": 0.2, "energy": 0.5, "complexity": 0.8, "humour": 0.0, "directness": 0.8},
    "empathetic":    {"formality": 0.3,  "warmth": 0.95,"energy": 0.3, "complexity": 0.3, "humour": 0.2, "directness": 0.3},
    "innovative":    {"formality": 0.4,  "warmth": 0.5, "energy": 0.8, "complexity": 0.6, "humour": 0.4, "directness": 0.7},
    "trustworthy":   {"formality": 0.6,  "warmth": 0.6, "energy": 0.3, "complexity": 0.5, "humour": 0.1, "directness": 0.6},
    "casual":        {"formality": 0.05, "warmth": 0.7, "energy": 0.6, "complexity": 0.2, "humour": 0.6, "directness": 0.5},
    "inspiring":     {"formality": 0.4,  "warmth": 0.7, "energy": 0.85,"complexity": 0.5, "humour": 0.2, "directness": 0.6},
}

# ── State ──────────────────────────────────────────────────────────────────────
class BrandVoiceState(TypedDict):
    # Inputs
    brand_name:     str   # brand name
    industry:       str   # industry category
    audience:       str   # target audience description
    tone_keywords:  str   # comma-separated tone adjectives (3–6)
    brand_values:   str   # optional — core brand values
    sample_content: str   # optional — existing copy to analyse
    output_type:    str   # type of voice guide output
    thread_id:      str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    voice_spectrum:  dict  # dimension → position 0–100 (owner: this node)
    parsed_keywords: list  # cleaned keyword list (owner: this node)

    # Outputs
    voice_guide: str   # full voice guide (owner: this node)
    error:       str   # failure reason if any (owner: this node)


# ── Phase 1 — pure voice spectrum mapping (no Claude) ────────────────────────

def _map_voice_spectrum(tone_keywords: str) -> tuple[dict, list]:
    """
    Phase 1 — map tone keywords to a 6-dimension voice spectrum.
    Returns (voice_spectrum, parsed_keywords). Pure function — no Claude.
    """
    keywords = [k.strip().lower() for k in tone_keywords.split(",") if k.strip()]
    spectrum: dict[str, float] = {dim: 0.5 for dim in _SPECTRUM_POLES}  # start at midpoint

    matched = 0
    for kw in keywords:
        if kw in _KEYWORD_SIGNALS:
            signals = _KEYWORD_SIGNALS[kw]
            for dim, val in signals.items():
                spectrum[dim] = (spectrum[dim] + val) / 2  # blend toward keyword signal
            matched += 1

    # Convert to 0–100 integer for readability
    spectrum_pct = {dim: round(val * 100) for dim, val in spectrum.items()}
    return spectrum_pct, keywords


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    brand_name: str,
    industry: str,
    audience: str,
    tone_keywords: str,
    brand_values: str,
    sample_content: str,
    output_type: str,
    voice_spectrum: dict,
    parsed_keywords: list,
) -> str:
    """Pure function — assembles the brand voice brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = output_type.replace("_", " ").title()
    values_str    = f"\nBrand values: {brand_values}" if brand_values else ""
    sample_str    = f"\nExisting copy to analyse:\n{sample_content[:1500]}" if sample_content else ""

    spectrum_str  = "\n".join(
        f"  {dim}: {pos}/100 — {poles[0]} ←→ {poles[1]}"
        for (dim, poles), pos in zip(_SPECTRUM_POLES.items(), voice_spectrum.values())
    )

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} brand strategist.

Brand          : {brand_name}
Industry       : {industry}
Audience       : {audience}
Tone keywords  : {', '.join(parsed_keywords)}{values_str}{sample_str}

Pre-computed voice spectrum (0 = pole A, 100 = pole B):
{spectrum_str}

Output required: {output_label}

FOR FULL_GUIDE — write a complete brand voice guide:
1. BRAND VOICE OVERVIEW (2 paragraphs — who this brand sounds like and why)
2. THE FOUR PILLARS (4 voice attributes, each with: definition, why it matters, what it looks like, what it doesn't look like)
3. DO / DON'T TABLE (10 rows — specific word choices, sentence structures, punctuation habits)
4. VOCABULARY (20 words we use | 20 words we avoid | 5 phrases that are very "us")
5. CHANNEL ADAPTATION (how voice adapts for: website, social, email, ads, customer support)
6. EXAMPLE REWRITES (3 before/after examples — same message, wrong voice → right voice)
7. VOICE CHECK QUESTIONS (5 questions any writer can ask: "Does this sound like [brand name]?")

FOR VOICE_SPECTRUM_ONLY:
Explain each of the 6 dimensions for this specific brand. For each: what the position means, why it fits, what it would look like in practice.

FOR DO_DONT_TABLE:
20-row table. Columns: Topic | Do | Don't | Example. Cover: vocabulary, grammar, punctuation, emoji, sentence length, humour, claims, CTAs, objection handling, apologies.

FOR MESSAGING_HIERARCHY:
- Brand promise (1 sentence — the big emotional payoff)
- Brand pillars (3 supporting proof points)
- Proof statements (2 per pillar)
- Tagline options (5 options — different angles)
- Elevator pitch versions: 10 words | 30 words | 100 words

FOR CHANNEL_ADAPTATION_GUIDE:
For each of 6 channels (website, Instagram, LinkedIn, email, ads, support):
  Voice dial settings for that channel
  Tone adjustments (same brand, different volume)
  Channel-specific do's and don'ts
  2 example messages

FOR REWRITE_SAMPLES:
Take the provided sample content and rewrite it 3 ways:
  - Too formal (wrong)
  - Too casual (wrong)
  - On-brand (right)
Then explain what makes the third version correct.

Every example must use {brand_name}'s actual industry and audience — no generic examples."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_guide(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def brand_voice_guide_node(state: BrandVoiceState) -> BrandVoiceState:
    thread_id      = state.get("thread_id", "unknown")
    brand_name     = state.get("brand_name", "").strip()
    industry       = state.get("industry", "general").lower().strip()
    audience       = state.get("audience", "").strip()
    tone_keywords  = state.get("tone_keywords", "").strip()
    brand_values   = state.get("brand_values", "").strip()
    sample_content = state.get("sample_content", "").strip()
    output_type    = state.get("output_type", "full_guide").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not brand_name:
        return {**state, "error": "PERMANENT: brand_name is required"}
    if not audience:
        return {**state, "error": "PERMANENT: audience is required"}
    if not tone_keywords:
        return {**state, "error": "PERMANENT: tone_keywords is required (comma-separated adjectives)"}
    if industry not in VALID_INDUSTRIES:
        return {**state, "error": f"PERMANENT: industry '{industry}' not in {VALID_INDUSTRIES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    kw_count = len([k for k in tone_keywords.split(",") if k.strip()])
    if kw_count > TONE_KEYWORD_LIMIT:
        return {**state, "error": f"PERMANENT: {kw_count} tone keywords exceeds TONE_KEYWORD_LIMIT={TONE_KEYWORD_LIMIT}"}

    # ── Phase 1 — pure voice spectrum mapping ─────────────────────────────────
    voice_spectrum, parsed_keywords = _map_voice_spectrum(tone_keywords)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        brand_name, industry, audience, tone_keywords, brand_values,
        sample_content, output_type, voice_spectrum, parsed_keywords,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "industry": industry,
        "output_type": output_type,
        "keyword_count": kw_count,
        "has_sample_content": bool(sample_content),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        voice_guide = _write_guide(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "guide_chars": len(voice_guide),
        "spectrum_keys": list(voice_spectrum.keys()),
        "output_type": output_type,
    })

    return {
        **state,
        "voice_guide":     voice_guide,
        "voice_spectrum":  voice_spectrum,
        "parsed_keywords": parsed_keywords,
        "error":           "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(BrandVoiceState)
    g.add_node("brand_voice_guide", brand_voice_guide_node)
    g.set_entry_point("brand_voice_guide")
    g.add_edge("brand_voice_guide", END)
    return g.compile()
