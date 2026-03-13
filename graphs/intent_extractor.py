"""
Ralph Loop 4 — Intent Extraction Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bridges natural language tasks → structured TypedDict fields.
Inserted between supervisor routing and agent execution.

The supervisor calls:
    fields = extract_intent(task, brief, role)

Then merges the extracted fields into the agent's state dict.
"""
from __future__ import annotations

import json
import structlog
import anthropic
from datetime import date

log = structlog.get_logger(__name__)

_client = anthropic.Anthropic()

# ── Schema Registry ────────────────────────────────────────────────────────
# Maps each agent role to its required input fields + descriptions.
# This is the single source of truth for what each agent needs.

AGENT_SCHEMAS: dict[str, dict[str, str]] = {
    "launch_orchestrator": {
        "product_name": "Name of the product, feature, or event being launched",
        "launch_type":  "Type of launch — MUST be exactly one of: product, feature, event, partnership, rebrand, campaign",
        "channels":     "Comma-separated marketing channels or 'all'",
        "launch_date":  "ISO date YYYY-MM-DD for the launch (infer 90 days from now if not specified)",
        "audience":     "Target audience description",
    },
    "product_strategist": {
        "product_name":    "Name of the product or concept",
        "market_context":  "Market or industry context",
        "strategy_focus":  "What aspect of strategy to focus on: positioning, roadmap, competitive, full",
    },
    "pricing_strategist": {
        "product_name":    "Name of the product or service",
        "market_segment":  "Target market segment",
        "competitor_info": "Known competitor pricing or 'unknown'",
        "pricing_goal":    "Goal: maximize_revenue, penetration, premium, freemium",
    },
    "competitive_analyst": {
        "product_name":    "Product or company to analyze",
        "industry":        "Industry or market vertical",
        "competitors":     "Known competitors (comma-separated) or 'discover'",
        "analysis_depth":  "shallow, standard, or deep",
    },
    "seo_analyst": {
        "url":          "Website URL to analyze",
        "focus_areas":  "Comma-separated: technical, content, backlinks, local, all",
        "target_market": "Geographic/demographic target",
    },
    "proposal_writer": {
        "client_name":     "Name of the client or prospect",
        "project_scope":   "What the proposal covers",
        "budget_range":    "Budget range if known, or 'tbd'",
        "timeline":        "Expected timeline",
    },
    "case_study_writer": {
        "client_name":     "Client or project name",
        "project_type":    "Type of project",
        "key_results":     "Key outcomes or metrics",
    },
    "venture_ideator": {
        "idea_context":    "Domain, trend, or problem space",
        "constraints":     "Budget, tech, or market constraints",
        "target_market":   "Who this would serve",
    },
    "investor_pitch_writer": {
        "company_name":    "Company or product name",
        "stage":           "Stage: pre-seed, seed, series_a, series_b",
        "ask_amount":      "Funding amount sought",
        "market":          "Target market",
    },
    "course_designer": {
        "course_topic":    "Subject of the course",
        "target_audience": "Who the course is for",
        "duration":        "Expected course length",
        "format":          "Format: video, text, hybrid, cohort",
    },
    "brand_voice_guide": {
        "brand_name":      "Name of the brand",
        "industry":        "Industry or vertical",
        "target_audience": "Primary audience",
        "tone_direction":  "Desired tone: professional, casual, bold, warm, authoritative",
    },
    "ecommerce_strategist": {
        "store_name":      "Store or brand name",
        "product_category": "What is being sold",
        "platform":        "Platform: shopify, woocommerce, custom, undecided",
        "market":          "Target market",
    },
}


def extract_intent(
    task: str,
    brief: str,
    role: str,
    *,
    model: str = "claude-sonnet-4-20250514",
) -> dict[str, str]:
    """
    Use Claude to extract structured fields from a natural language task.

    Returns a dict of field_name -> extracted_value for the target agent.
    Falls back to sensible defaults if extraction fails.
    """
    schema = AGENT_SCHEMAS.get(role)
    if not schema:
        log.info("intent_extractor.no_schema", role=role)
        return {}

    today = date.today().isoformat()

    fields_desc = "\n".join(
        f"  - {k}: {v}" for k, v in schema.items()
    )

    prompt = f"""You are an intent extraction system. Given a natural language task,
extract the structured fields needed by the '{role}' agent.

Today's date: {today}

TASK: {task}
BRIEF: {brief}

REQUIRED FIELDS:
{fields_desc}

RULES:
- Extract or infer each field from the task and brief text.
- If a date is not specified, infer a reasonable one (e.g. 90 days from today for launches).
- If a field truly cannot be inferred, use a sensible default.
- For comma-separated fields, provide clean comma-separated values.
- Return ONLY valid JSON with the field names as keys. No markdown, no explanation.

JSON:"""

    try:
        resp = _client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        extracted = json.loads(raw)
        log.info("intent_extractor.success", role=role, fields=list(extracted.keys()))
        return extracted

    except Exception as e:
        log.error("intent_extractor.failed", role=role, error=str(e))
        # Return empty — caller should fall back to general_assistant
        return {}
