"""
Fullstack Architect — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), stack_context (str), output_type (VALID_OUTPUT_TYPES), framework (VALID_FRAMEWORKS)
    Outputs: architecture_doc (str), stack_decision (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/framework → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before stack analysis
    POST — architecture_doc + stack_decision persisted after successful generation
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

ROLE        = "fullstack_architect"
MAX_RETRIES = 3
MAX_TOKENS  = 2800

VALID_OUTPUT_TYPES = {
    "architecture_doc", "component_spec", "api_design", "data_flow_diagram",
    "tech_stack_decision", "refactor_plan", "general",
}
VALID_FRAMEWORKS = {
    "nextjs", "remix", "sveltekit", "nuxt", "astro",
    "express", "fastapi", "django", "rails", "general",
}

# ── Stack Profiles ─────────────────────────────────────────────────────────────
_STACK_PROFILES = {
    "nextjs": {
        "language":   "TypeScript",
        "rendering":  "SSR / SSG / ISR / RSC",
        "state":      "Zustand / Jotai / TanStack Query",
        "styling":    "Tailwind CSS v4",
        "db":         "Supabase / Prisma / Drizzle",
        "deployment": "Vercel",
        "strengths":  ["SEO-ready", "edge-native", "full-stack in one repo"],
        "pitfalls":   ["bundle bloat without tree-shaking", "RSC mental model shift", "cold starts on heavy pages"],
    },
    "fastapi": {
        "language":   "Python 3.11+",
        "rendering":  "API only (JSON)",
        "state":      "stateless / Redis for sessions",
        "styling":    "N/A",
        "db":         "SQLAlchemy / asyncpg / Supabase",
        "deployment": "Docker / Railway / Fly.io",
        "strengths":  ["async-first", "automatic OpenAPI docs", "type-safe with Pydantic"],
        "pitfalls":   ["GIL for CPU-bound tasks", "dependency injection complexity"],
    },
    "general": {
        "language":   "TypeScript / Python",
        "rendering":  "context-dependent",
        "state":      "framework-appropriate",
        "styling":    "Tailwind CSS",
        "db":         "Supabase / PostgreSQL",
        "deployment": "Vercel / Railway",
        "strengths":  ["flexible", "proven patterns"],
        "pitfalls":   ["technology mismatch if not validated upfront"],
    },
}

_ARCHITECTURE_PRINCIPLES = [
    "Type-safety end-to-end (Zod / Pydantic validation at every boundary)",
    "Separation of concerns — UI / business logic / data access layers distinct",
    "API contract-first — define schema before implementation",
    "Error boundaries at every async boundary",
    "Environment-aware config — never hardcode secrets",
    "Progressive enhancement — core functionality without JS where possible",
    "Optimistic UI with rollback for all mutations",
]


class FullstackArchitectState(TypedDict, total=False):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    task:             str
    stack_context:    str
    output_type:      str
    framework:        str
    architecture_doc: str
    stack_decision:   str


# ── Phase 1 — Stack Analysis (pure, no Claude) ────────────────────────────────
def _analyse_stack(framework: str, task: str) -> dict:
    """Returns stack_data dict — pure lookup, no Claude."""
    profile   = _STACK_PROFILES.get(framework, _STACK_PROFILES["general"])
    task_lower = task.lower()
    flags: list[str] = []
    if "real" in task_lower and "time" in task_lower:
        flags.append("Consider WebSockets / SSE / Supabase Realtime")
    if "auth" in task_lower:
        flags.append("Auth: Supabase Auth / NextAuth / Clerk recommended")
    if "payment" in task_lower:
        flags.append("Payment: Stripe — use webhooks not polling")
    if "upload" in task_lower or "file" in task_lower:
        flags.append("File handling: Supabase Storage / S3 — stream, don't buffer")
    if "search" in task_lower:
        flags.append("Search: Supabase pg_vector / Algolia / Typesense")
    return {**profile, "flags": flags, "principles": _ARCHITECTURE_PRINCIPLES}

_build_prompt = None  # assigned below


# ── Phase 2 — Claude Architecture Doc ─────────────────────────────────────────
def _build_arch_prompt(state: FullstackArchitectState, stack_data: dict) -> str:
    persona      = get_persona(ROLE)
    task         = state["task"]
    stack_ctx    = state.get("stack_context", "")
    output_type  = state.get("output_type", "architecture_doc")
    framework    = state.get("framework", "general")

    flags_text = "
".join(f"  ⚡ {f}" for f in stack_data["flags"]) or "  None detected"
    principles = "
".join(f"  • {p}" for p in stack_data["principles"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Produce a production-grade {output_type} for the task below.

FRAMEWORK: {framework}
STACK PROFILE:
  Language:   {stack_data['language']}
  Rendering:  {stack_data['rendering']}
  State:      {stack_data['state']}
  Styling:    {stack_data['styling']}
  Database:   {stack_data['db']}
  Deployment: {stack_data['deployment']}
  Strengths:  {', '.join(stack_data['strengths'])}
  Pitfalls:   {', '.join(stack_data['pitfalls'])}

ARCHITECTURE FLAGS:
{flags_text}

MANDATORY PRINCIPLES:
{principles}

TASK:
{task}

ADDITIONAL CONTEXT:
{stack_ctx or "None provided"}

OUTPUT FORMAT:
## Fullstack Architecture: {output_type.replace('_', ' ').title()}

### Overview
[2–3 sentences — what this builds and why this approach]

### Component Structure
[Directory tree + responsibility of each layer]

### Data Flow
[Request lifecycle — client → API → DB → response, with type annotations]

### Key Technical Decisions
| Decision | Choice | Rationale | Trade-offs |
|---|---|---|---|
[3–5 rows]

### Implementation Phases
**Phase 1 (Foundation):** [3–4 tasks]
**Phase 2 (Features):** [3–4 tasks]
**Phase 3 (Production-ready):** [3–4 tasks]

### Pitfall Mitigations
[One mitigation per identified pitfall]

### Type Contracts
```typescript
// Key interfaces / Zod schemas
```

### Next Action
[Single most important first step]
"""

_build_prompt = _build_arch_prompt  # spec alias


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


def fullstack_architect_node(state: FullstackArchitectState) -> FullstackArchitectState:
    thread_id   = state.get("workflow_id", "local")
    task        = state.get("task", "").strip()
    output_type = state.get("output_type", "architecture_doc")
    framework   = state.get("framework", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if output_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}")
    if framework not in VALID_FRAMEWORKS:
        raise ValueError(f"PERMANENT: framework '{framework}' not in {VALID_FRAMEWORKS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": output_type, "framework": framework})

    stack_data = _analyse_stack(framework, task)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_arch_prompt(state, stack_data)

    try:
        doc = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    decision_match = re.search(r'### Key Technical Decisions([\s\S]+?)(?=###|$)', doc)
    stack_decision = decision_match.group(1).strip() if decision_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": output_type, "framework": framework})

    return {
        **state,
        "agent":            ROLE,
        "architecture_doc": doc,
        "stack_decision":   stack_decision,
        "error":            None,
    }
