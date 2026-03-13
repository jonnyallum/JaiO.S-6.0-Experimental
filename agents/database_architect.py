"""
Database Architect - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), db_context (str), output_type (VALID_OUTPUT_TYPES), db_engine (VALID_DB_ENGINES)
    Outputs: db_design (str), schema_summary (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/db_engine → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before schema analysis
    POST - db_design + schema_summary persisted after successful generation
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

ROLE        = "database_architect"
MAX_RETRIES = 3
MAX_TOKENS  = 2600

VALID_OUTPUT_TYPES = {
    "schema_design", "migration_plan", "rls_policy", "index_strategy",
    "query_optimisation", "data_model_review", "general",
}
VALID_DB_ENGINES = {
    "postgresql", "supabase", "mysql", "sqlite", "mongodb", "redis", "general",
}

# ── DB Design Heuristics ───────────────────────────────────────────────────────
_NORMAL_FORM_CHECKS = [
    ("UUID primary keys",          r'serial|integer\s+primary\s+key', "Use UUID v4 or gen_random_uuid() - portability + security"),
    ("Timestamp audit columns",    r'created_at|updated_at',                   "Every table needs created_at + updated_at (auto-managed by trigger)"),
    ("Soft delete pattern",        r'deleted_at|is_deleted',                   "Prefer deleted_at (soft delete) over hard DELETE for audit trails"),
    ("No nullable FKs without reason", r'references\s+\w+\s*\(',             "Nullable FK = optional relationship - document the business reason"),
]

_RLS_PATTERNS = {
    "user_owned":    "auth.uid() = user_id",
    "org_scoped":    "auth.uid() IN (SELECT user_id FROM org_members WHERE org_id = org_id)",
    "public_read":   "true  -- SELECT policy only; mutations require auth",
    "admin_only":    "auth.jwt() ->> 'role' = 'admin'",
    "service_role":  "auth.role() = 'service_role'",
}

_INDEX_RULES = [
    "Index every FK column (join performance)",
    "Composite index: (user_id, created_at) for paginated user feeds",
    "Partial index: WHERE deleted_at IS NULL for soft-delete tables",
    "GIN index for JSONB columns queried with @> or ?",
    "pg_trgm index for LIKE/ILIKE search on text columns",
    "Avoid over-indexing - each index slows writes",
]


class DatabaseArchitectState(TypedDict, total=False):
    workflow_id:    str
    timestamp:      str
    agent:          str
    error:          str | None
    task:           str
    db_context:     str
    output_type:    str
    db_engine:      str
    db_design:      str
    schema_summary: str


# ── Phase 1 - Schema Heuristics (pure, no Claude) ─────────────────────────────
def _analyse_requirements(task: str, db_engine: str) -> dict:
    """Returns requirements_data dict - pure heuristic analysis."""
    task_lower = task.lower()
    flags: list[str] = []

    if any(w in task_lower for w in ["user", "account", "auth", "login"]):
        flags.append("Users table required - reference Supabase auth.users via FK")
    if any(w in task_lower for w in ["tenant", "organisation", "org", "team"]):
        flags.append("Multi-tenant pattern - add org_id to all user-scoped tables")
    if any(w in task_lower for w in ["payment", "invoice", "subscription"]):
        flags.append("Financial data - use NUMERIC not FLOAT for monetary values")
    if any(w in task_lower for w in ["file", "upload", "image", "attachment"]):
        flags.append("Store file metadata in DB, actual files in object storage (Supabase Storage / S3)")
    if any(w in task_lower for w in ["search", "fulltext", "fts"]):
        flags.append("Full-text search - add tsvector column with GIN index + pg_trgm extension")
    if any(w in task_lower for w in ["realtime", "live", "notification"]):
        flags.append("Enable Supabase Realtime on tables requiring live updates")
    if any(w in task_lower for w in ["audit", "history", "log", "track"]):
        flags.append("Audit log pattern - separate audit_log table with trigger-based writes")

    supabase_specific = db_engine == "supabase"
    return {
        "flags":             flags,
        "rls_patterns":      _RLS_PATTERNS,
        "index_rules":       _INDEX_RULES,
        "normal_form_rules": _NORMAL_FORM_CHECKS,
        "supabase_mode":     supabase_specific,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude DB Design ─────────────────────────────────────────────────
def _build_db_prompt(state: DatabaseArchitectState, req_data: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    db_ctx      = state.get("db_context", "")
    output_type = state.get("output_type", "schema_design")
    db_engine   = state.get("db_engine", "postgresql")

    flags_text  = "\n".join(f"  ⚡ {f}" for f in req_data["flags"]) or "  None detected"
    index_text  = "\n".join(f"  • {r}" for r in req_data["index_rules"])
    supabase_note = "Supabase-specific: Use gen_random_uuid(), auth.uid() in RLS, enable Realtime where needed." if req_data["supabase_mode"] else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Design a production-grade {output_type} for the requirements below.

DATABASE ENGINE: {db_engine}
{supabase_note}

REQUIREMENT FLAGS:
{flags_text}

INDEX RULES TO APPLY:
{index_text}

TASK:
{task}

ADDITIONAL CONTEXT:
{db_ctx or "None provided"}

OUTPUT FORMAT:
## Database Design: {output_type.replace('_', ' ').title()}

### Entity Overview
[Table list with one-line purpose each]

### Schema (SQL)
```sql
-- Full CREATE TABLE statements with constraints, indexes, triggers
```

### Relationships
[ERD-style text: table → (FK) → table, cardinality]

### RLS Policies (if Supabase/PostgreSQL)
```sql
-- One policy block per table
```

### Index Strategy
[Table → indexes with justification]

### Migration Notes
[Breaking changes, backfill requirements, zero-downtime approach]

### Performance Considerations
[Query patterns to watch, N+1 risks, caching opportunities]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_db_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
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


def database_architect_node(state: DatabaseArchitectState) -> DatabaseArchitectState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    output_type = state.get("output_type", "schema_design")
    db_engine   = state.get("db_engine", "postgresql")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")
    if db_engine not in VALID_DB_ENGINES:
        raise ValueError(f"PERMANENT: db_engine '{db_engine}' not in {VALID_DB_ENGINES}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": output_type, "db_engine": db_engine})

    req_data = _analyse_requirements(task, db_engine)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_db_prompt(state, req_data)

    try:
        design = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    summary_match = re.search(r'### Entity Overview([\s\S]+?)(?=###|$)', design)
    schema_summary = summary_match.group(1).strip() if summary_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": output_type, "db_engine": db_engine})

    return {
        **state,
        "agent":          ROLE,
        "db_design":      design,
        "schema_summary": schema_summary,
        "error":          None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(DatabaseArchitectState)
    g.add_node("database_architect", database_architect_node)
    g.set_entry_point("database_architect")
    g.add_edge("database_architect", END)
    return g.compile()
