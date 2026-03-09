"""
Skill: Data Extraction
Role: data_extraction

Extracts structured data from raw inputs and validates against schemas.
Supports JSON, YAML, and free-text extraction via Claude.

Persona injected at runtime via personas/config.py.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing_extensions import TypedDict

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()
ROLE = "data_extraction"


class DataExtractionState(BaseState):
    raw_input: str           # Raw text or data to parse
    schema: dict             # Expected output fields: {field_name: description}
    extraction_mode: str     # json | structured | freetext
    parsed_data: dict        # Extracted structured output
    validation_passed: bool


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=45),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _ask_claude(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _try_parse_json(text: str) -> Optional[dict]:
    """Extract JSON from a string, stripping markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _validate_schema(data: dict, schema: dict) -> tuple[bool, list[str]]:
    """Check all schema keys are present and non-null."""
    issues = []
    for field, description in schema.items():
        if field not in data:
            issues.append(f"Missing: {field} ({description})")
        elif data[field] is None:
            issues.append(f"Null value: {field}")
    return len(issues) == 0, issues


def data_extraction_node(state: DataExtractionState) -> dict:
    """
    Data Extraction skill node.
    Extracts structured data from raw input per the provided schema.
    Validates output and reports any issues.
    """
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    mode = state.get("extraction_mode", "structured")
    persona = get_persona(ROLE)

    log.info(f"{ROLE}.started", workflow_id=workflow_id, mode=mode)

    state_logger = SupabaseStateLogger()

    try:
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        schema_description = "\n".join(
            f"  - {field}: {desc}" for field, desc in state["schema"].items()
        )

        prompt = f"""{persona['personality']}

Extract structured data from the input below.
Return ONLY valid JSON matching the schema. No explanation, no markdown fences, no preamble.

━━━ SCHEMA ━━━
{schema_description}

━━━ RAW INPUT ━━━
{state['raw_input'][:5000]}

━━━ RULES ━━━
- Return ONLY a JSON object with exactly the schema fields.
- If a field cannot be determined, use null.
- Do not add extra fields.
- Ensure all strings are properly escaped.

JSON output:"""

        response_text = _ask_claude(claude, prompt)
        parsed = _try_parse_json(response_text)

        if parsed is None:
            # One retry with stricter prompt
            retry_prompt = (
                f"Return ONLY a raw JSON object (no markdown, no explanation) "
                f"with these exact keys: {list(state['schema'].keys())}. "
                f"Source: {state['raw_input'][:2000]}"
            )
            response_text = _ask_claude(claude, retry_prompt)
            parsed = _try_parse_json(response_text)

        if parsed is None:
            raise ValueError(
                f"Could not extract valid JSON after 2 attempts. Last response: {response_text[:200]}"
            )

        validation_passed, issues = _validate_schema(parsed, state["schema"])

        if not validation_passed:
            log.warning(f"{ROLE}.validation_issues", issues=issues)

        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id=f"{ROLE}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            agent=ROLE,
            state={
                "mode": mode,
                "fields_extracted": len(parsed),
                "validation_passed": validation_passed,
                "validation_issues": issues,
                "status": "completed",
            },
        )

        log.info(f"{ROLE}.completed", fields=len(parsed), validation_passed=validation_passed)
        return {
            "parsed_data": parsed,
            "validation_passed": validation_passed,
            "error": None if validation_passed else f"Validation: {'; '.join(issues)}",
            "workflow_id": workflow_id,
        }

    except Exception as exc:
        msg = f"{ROLE} error: {exc}"
        log.exception(f"{ROLE}.error", error=msg)
        return {"parsed_data": {}, "validation_passed": False, "error": msg}


# Backwards-compatibility aliases
parser_node = data_extraction_node
ParserState = DataExtractionState
