"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : data_parser
 SKILL : Data Parser — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : raw_data (str), input_format (str),
               output_schema (str), schema_description (str),
               strict_mode (bool — default True),
               sample_only (bool — default False)
 Output keys : parsed_output (str), field_map (dict),
               exception_log (list[str])
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 format detection + field
 inventory → Phase 2 Claude schema contract + parsing logic.
 PARSE_ATTEMPTS = 1. Strict mode blocks unknown fields.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid input_format/output_schema (ValueError),
               empty raw_data, undetectable format after Phase 1
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: input_format, output_schema,
        detected_fields, strict_mode
 POST — logged after success: parsed char count, exception count

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import json
import re
from typing import Optional

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

ROLE = "data_parser"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES    = 3
MAX_TOKENS     = 2400
PARSE_ATTEMPTS = 1
DATA_PREVIEW   = 3000   # chars of raw_data sent to Claude

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_INPUT_FORMATS = {
    "json", "csv", "xml", "yaml", "markdown_table",
    "freeform_text", "html_table", "auto"
}
VALID_OUTPUT_SCHEMAS = {
    "flat_json", "nested_json", "csv",
    "pydantic_model", "typed_dataclass", "sql_insert", "jsonl"
}

# ── Exception classification taxonomy ─────────────────────────────────────────
_EXCEPTION_TYPES: dict[str, str] = {
    "MISSING_REQUIRED":  "Required field absent in source — cannot map",
    "TYPE_MISMATCH":     "Value present but wrong type (e.g. str where int expected)",
    "AMBIGUOUS_FIELD":   "Field name matches multiple schema targets",
    "EXTRA_FIELD":       "Source field has no mapping in target schema",
    "NULL_VALUE":        "Field present but null/empty — check if nullable",
    "FORMAT_VIOLATION":  "Value doesn't match expected format (date, email, URL, etc.)",
    "ENCODING_ISSUE":    "Non-UTF-8 characters or BOM detected",
}

# ── State ──────────────────────────────────────────────────────────────────────
class DataParserState(BaseState):
    # Inputs
    raw_data:          str   # raw input data to parse
    input_format:      str   # format of raw_data
    output_schema:     str   # target output format
    schema_description:str   # description of the target schema fields
    strict_mode:       bool  # True = unknown fields raise EXTRA_FIELD exception
    sample_only:       bool  # True = return schema contract only, not full parse
    thread_id:         str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    detected_format: str        # auto-detected format if input_format="auto" (owner: this node)
    field_map:       dict       # detected field → type mapping (owner: this node)
    field_count:     int        # number of fields detected (owner: this node)

    # Outputs
    parsed_output:  str         # parsed data in target schema (owner: this node)
    exception_log:  list[str]   # classified exceptions found (owner: this node)
    error:          str         # failure reason if any (owner: this node)


# ── Phase 1 — pure format detection + field inventory (no Claude) ─────────────

def _detect_format(raw_data: str) -> str:
    """Phase 1 — heuristic format detection. Pure function — no Claude."""
    stripped = raw_data.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            pass
    if stripped.startswith("<") and ">" in stripped:
        return "xml"
    if re.match(r'^[a-zA-Z_][^,\n]+(?:,[^,\n]+){2,}', stripped.split("\n")[0] or ""):
        return "csv"
    if re.match(r'^\s*\|', stripped):
        return "markdown_table"
    if re.match(r'^[a-zA-Z_\-]+\s*:', stripped, re.MULTILINE):
        return "yaml"
    return "freeform_text"


def _inventory_fields(raw_data: str, fmt: str) -> dict:
    """
    Phase 1 — extract field names and inferred types from the data.
    Returns {field_name: inferred_type}. Pure function — no Claude.
    """
    fields: dict[str, str] = {}

    if fmt == "json":
        try:
            obj = json.loads(raw_data.strip())
            if isinstance(obj, list) and obj:
                obj = obj[0]
            if isinstance(obj, dict):
                for k, v in obj.items():
                    fields[k] = type(v).__name__
        except Exception:
            pass

    elif fmt == "csv":
        lines = raw_data.strip().split("\n")
        if lines:
            headers = [h.strip().strip('"') for h in lines[0].split(",")]
            # Infer types from second row if present
            if len(lines) > 1:
                vals = [v.strip().strip('"') for v in lines[1].split(",")]
                for h, v in zip(headers, vals):
                    if re.match(r'^-?\d+\.\d+$', v):
                        fields[h] = "float"
                    elif re.match(r'^-?\d+$', v):
                        fields[h] = "int"
                    elif re.match(r'^\d{4}-\d{2}-\d{2}', v):
                        fields[h] = "date"
                    else:
                        fields[h] = "str"
            else:
                for h in headers:
                    fields[h] = "unknown"

    elif fmt == "markdown_table":
        lines = [l.strip() for l in raw_data.strip().split("\n") if l.strip().startswith("|")]
        if lines:
            headers = [h.strip() for h in lines[0].strip("|").split("|")]
            for h in headers:
                fields[h] = "str"

    return fields


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    raw_data: str,
    input_format: str,
    output_schema: str,
    schema_description: str,
    strict_mode: bool,
    sample_only: bool,
    field_map: dict,
) -> str:
    """Pure function — assembles the parsing contract brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    strict_note  = "STRICT MODE: flag every unmapped or extra field as an exception." if strict_mode else "LENIENT MODE: skip unknown fields silently."
    sample_note  = "SAMPLE ONLY: return the schema contract and field mapping — do NOT parse all data." if sample_only else "Parse the full dataset."
    exception_taxonomy = "\n".join(f"  {k}: {v}" for k, v in _EXCEPTION_TYPES.items())
    detected_str = "\n".join(f"  {k}: {v}" for k, v in field_map.items()) if field_map else "  (could not auto-detect — infer from data)"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} data parsing and schema specialist.

Input format   : {input_format}
Output schema  : {output_schema}
Strict mode    : {strict_mode} — {strict_note}
{sample_note}

Target schema description:
{schema_description}

Pre-detected fields in source data:
{detected_str}

Raw data (preview — first {DATA_PREVIEW} chars):
{raw_data[:DATA_PREVIEW]}

Exception taxonomy to use:
{exception_taxonomy}

Deliver:

1. CANONICAL SCHEMA CONTRACT
   For each target field:
   | Field | Type | Required | Source Field | Transform | Default |
   One row per field. If no source mapping exists, mark Source Field as "MISSING_REQUIRED" or "EXTRA_FIELD".

2. FIELD MAPPING LOGIC
   For each non-trivial mapping, write the transformation rule:
   e.g. "source.created_at (ISO string) → target.date (datetime) — parse with dateutil.parser.parse()"

3. EXCEPTION LOG
   List every anomaly found, classified using the taxonomy above:
   [EXCEPTION_TYPE] field_name: description

4. PARSED OUTPUT
   {"(Schema contract only — no parsed output in SAMPLE ONLY mode)" if sample_only else f"The full dataset converted to {output_schema} format."}
   If output_schema is pydantic_model or typed_dataclass: write the class definition with type annotations.
   If output_schema is flat_json/nested_json/jsonl: write the converted data.
   If output_schema is csv: write the CSV with headers.
   If output_schema is sql_insert: write the INSERT statements.

5. VALIDATION RULES
   3–5 data quality assertions to run post-parse (e.g. "assert all prices > 0", "assert no null IDs")

Be precise. No approximations. Every field must have an explicit mapping decision."""


def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _parse_data(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text
_generate = _parse_data  # spec alias



def _extract_exceptions(parsed_output: str) -> list[str]:
    """Phase 1 (post) — extract exception lines from Claude output. Pure function."""
    lines = parsed_output.split("\n")
    return [l.strip() for l in lines if any(t in l for t in _EXCEPTION_TYPES)]


# ── Node ───────────────────────────────────────────────────────────────────────

def data_parser_node(state: DataParserState) -> DataParserState:
    thread_id          = state.get("thread_id", "unknown")
    raw_data           = state.get("raw_data", "").strip()
    input_format       = state.get("input_format", "auto").lower().strip()
    output_schema      = state.get("output_schema", "flat_json").lower().strip()
    schema_description = state.get("schema_description", "").strip()
    strict_mode        = bool(state.get("strict_mode", True))
    sample_only        = bool(state.get("sample_only", False))

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not raw_data:
        return {**state, "error": "PERMANENT: raw_data is required"}
    if not schema_description:
        return {**state, "error": "PERMANENT: schema_description is required — describe the target fields"}
    if input_format not in VALID_INPUT_FORMATS:
        return {**state, "error": f"PERMANENT: input_format '{input_format}' not in {VALID_INPUT_FORMATS}"}
    if output_schema not in VALID_OUTPUT_SCHEMAS:
        return {**state, "error": f"PERMANENT: output_schema '{output_schema}' not in {VALID_OUTPUT_SCHEMAS}"}

    # ── Phase 1 — pure format detection + field inventory ─────────────────────
    detected_format = _detect_format(raw_data) if input_format == "auto" else input_format
    field_map       = _inventory_fields(raw_data, detected_format)
    field_count     = len(field_map)

    if input_format == "auto" and not field_map:
        return {**state, "error": "PERMANENT: could not detect format or fields — specify input_format explicitly"}

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        raw_data, detected_format, output_schema,
        schema_description, strict_mode, sample_only, field_map,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "input_format":   detected_format,
        "output_schema":  output_schema,
        "detected_fields": field_count,
        "strict_mode":    strict_mode,
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        parsed_output = _parse_data(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    exception_log = _extract_exceptions(parsed_output)

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "parsed_chars":    len(parsed_output),
        "exception_count": len(exception_log),
        "field_count":     field_count,
    })

    return {
        **state,
        "parsed_output":  parsed_output,
        "field_map":      field_map,
        "exception_log":  exception_log,
        "detected_format": detected_format,
        "field_count":    field_count,
        "agent": ROLE,

        "error": None,
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(DataParserState)
    g.add_node("data_parser", data_parser_node)
    g.set_entry_point("data_parser")
    g.add_edge("data_parser", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
