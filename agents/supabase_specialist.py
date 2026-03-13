"""
Supabase Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), project_context (str), output_type (VALID_OUTPUT_TYPES), area (VALID_AREAS)
    Outputs: supabase_spec (str), sql_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/area → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before Supabase spec generation
    POST - supabase_spec + sql_output persisted after successful generation
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

ROLE        = "supabase_specialist"
MAX_RETRIES = 3
MAX_TOKENS  = 2600

VALID_OUTPUT_TYPES = {
    "rls_policy", "edge_function", "migration", "realtime_config",
    "storage_policy", "postgrest_query", "trigger", "cron_job", "general",
}
VALID_AREAS = {
    "auth", "database", "storage", "realtime", "edge_functions",
    "postgrest", "vault", "general",
}

# ── Supabase Knowledge Base ────────────────────────────────────────────────────
_RLS_TEMPLATES = {
    "user_owned_select":  "CREATE POLICY \"select_own\" ON {table} FOR SELECT USING (auth.uid() = user_id);",
    "user_owned_insert":  "CREATE POLICY \"insert_own\" ON {table} FOR INSERT WITH CHECK (auth.uid() = user_id);",
    "user_owned_update":  "CREATE POLICY \"update_own\" ON {table} FOR UPDATE USING (auth.uid() = user_id);",
    "user_owned_delete":  "CREATE POLICY \"delete_own\" ON {table} FOR DELETE USING (auth.uid() = user_id);",
    "public_read":        "CREATE POLICY \"public_read\" ON {table} FOR SELECT USING (true);",
    "service_role_write": "CREATE POLICY \"service_write\" ON {table} FOR ALL USING (auth.role() = \'service_role\');",
}

_EDGE_FUNCTION_TEMPLATE = """import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'
from langgraph.graph import StateGraph, END

serve(async (req: Request) => {
  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
  )
  // Implementation here
  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json' },
  })
})"""

_COMMON_PITFALLS = [
    "RLS disabled by default - always call ALTER TABLE ... ENABLE ROW LEVEL SECURITY",
    "anon key is public - never use service_role key client-side",
    "Realtime: enable replica identity FULL for UPDATE/DELETE events",
    "Storage: bucket policies are separate from table RLS",
    "Edge Functions: use SUPABASE_SERVICE_ROLE_KEY only server-side",
    "PostgREST: use exact column names - JS camelCase is NOT auto-converted",
    "Migrations: use supabase db push for local, supabase db push --db-url for remote",
    "auth.uid() returns null for unauthenticated - always handle the null case",
]

_AREA_CHECKLIST = {
    "auth":           ["confirm email enabled?", "JWT expiry set?", "redirect URLs whitelisted?", "MFA configured?"],
    "database":       ["RLS enabled on all tables?", "service_role key secured?", "connection pooling mode?", "pg_stat_statements enabled?"],
    "storage":        ["bucket public/private?", "file size limits set?", "allowed MIME types?", "RLS on objects table?"],
    "realtime":       ["replica identity FULL on target tables?", "channel filters defined?", "auth payload included?"],
    "edge_functions": ["env vars set in Supabase dashboard?", "CORS headers configured?", "rate limiting considered?"],
    "postgrest":      ["views exposed?", "functions as RPCs?", "schema exposed in config?"],
    "vault":          ["secrets stored via vault.create_secret?", "never in .env for production?"],
    "general":        ["project ref correct?", "anon/service role keys separated?"],
}


class SupabaseSpecialistState(TypedDict, total=False):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    task:             str
    project_context:  str
    output_type:      str
    area:             str
    supabase_spec:    str
    sql_output:       str


# ── Phase 1 - Supabase Context (pure, no Claude) ──────────────────────────────
def _build_supabase_context(area: str, output_type: str) -> dict:
    """Returns context dict - pure lookup, no Claude."""
    checklist = _AREA_CHECKLIST.get(area, _AREA_CHECKLIST["general"])
    return {
        "checklist":        checklist,
        "common_pitfalls":  _COMMON_PITFALLS,
        "rls_templates":    _RLS_TEMPLATES,
        "edge_template":    _EDGE_FUNCTION_TEMPLATE if output_type == "edge_function" else "",
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Supabase Spec ────────────────────────────────────────────
def _build_supabase_prompt(state: SupabaseSpecialistState, ctx: dict) -> str:
    persona     = get_persona(ROLE)
    task        = state["task"]
    proj_ctx    = state.get("project_context", "")
    output_type = state.get("output_type", "general")
    area        = state.get("area", "general")

    checklist_text = "\n".join(f"  ☐ {c}" for c in ctx["checklist"])
    pitfalls_text  = "\n".join(f"  ⚠ {p}" for p in ctx["common_pitfalls"][:5])

    edge_section = f"""
EDGE FUNCTION BOILERPLATE:
```typescript
{ctx['edge_template']}
```""" if ctx["edge_template"] else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Produce a production-ready {output_type} for Supabase - area: {area}.

AREA CHECKLIST:
{checklist_text}

COMMON PITFALLS TO AVOID:
{pitfalls_text}
{edge_section}

TASK:
{task}

PROJECT CONTEXT:
{proj_ctx or "None provided"}

OUTPUT FORMAT:
## Supabase Spec: {output_type.replace('_', ' ').title()}

### Overview
[What this implements and why]

### Implementation
```sql
-- SQL migrations / RLS policies / triggers
```

```typescript
// Edge Functions / client-side queries (if applicable)
```

### Checklist Verification
[Go through each checklist item - PASS / FAIL / N/A]

### Pitfall Mitigations
[Address each relevant pitfall with specific mitigation]

### Testing
[How to verify this works in Supabase local dev + production]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_supabase_prompt  # spec alias


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


def supabase_specialist_node(state: SupabaseSpecialistState) -> SupabaseSpecialistState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    output_type = state.get("output_type", "general")
    area        = state.get("area", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")
    if area not in VALID_AREAS:
        raise ValueError(f"PERMANENT: area '{area}' not in {VALID_AREAS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": output_type, "area": area})

    ctx = _build_supabase_context(area, output_type)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_supabase_prompt(state, ctx)

    try:
        spec = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    sql_match  = re.search(r'```sql([\s\S]+?)```', spec)
    sql_output = sql_match.group(1).strip() if sql_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": output_type, "area": area})

    return {
        **state,
        "agent":         ROLE,
        "supabase_spec": spec,
        "sql_output":    sql_output,
        "error":         None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(SupabaseSpecialistState)
    g.add_node("supabase_specialist", supabase_specialist_node)
    g.set_entry_point("supabase_specialist")
    g.add_edge("supabase_specialist", END)
    return g.compile()
