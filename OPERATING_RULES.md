# JaiO.S 6.0 — Operating Rules

> **This is the system contract.**
> Claude Code reads this before touching anything.
> Every contributor follows these rules without exception.

---

## 1. Architecture Overview

```
Incoming request (API / Telegram / n8n / Daemon)
        │
        ▼
  api/main.py  ──►  graphs/supervisor.py  ──►  agents/<skill>.py
        │                    │                        │
        │            routes by intent           Claude call
        │                    │                        │
        ▼                    ▼                        ▼
  Supabase jobs        LangGraph state          Supabase log
  table (queue)        checkpoint               (PRE / POST)
        │
        ▼
  Telegram alert (errors only)
```

**Rule:** Every job enters via `api/main.py`. Nothing bypasses the API.

---

## 2. Agent Contract

Every agent file in `agents/` MUST:

1. Have a docstring header with AGENT, SKILL, Node Contract, Failure Discrimination, Checkpoint Semantics
2. Import from: `config.settings`, `personas.config`, `state.base`, `tools.notification_tools`, `tools.supabase_tools`, `tools.telemetry`
3. Define a `TypedDict` State class extending `BaseState`
4. Define `_build_prompt(state, persona) -> str` — pure function, no I/O
5. Define `_generate(client, prompt, metrics) -> str` — decorated with `@retry` (tenacity)
6. Define `<role>_node(state) -> dict` — the LangGraph node function
7. Write PRE checkpoint before Claude call, POST checkpoint after
8. Handle three exception classes: `ValueError` (permanent), `anthropic.APIError` (transient), `Exception` (unexpected)
9. Return `{"<output_key>": result, "error": None, "workflow_id": thread_id, "agent": ROLE}` on success
10. Return `{"<output_key>": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}` on failure
11. Use model `claude-sonnet-4-6` unless explicitly overridden
12. Use `MAX_TOKENS` constant — never hardcode token limits
13. Contain **no human names, no nicknames, no references to any external system by name** in agent logic
14. Contain **no identity** — persona is injected at runtime via `personas/config.py`

---

## 3. Graph Contract

Every graph file in `graphs/` MUST:

1. Import only from `agents/` — never import agent internals directly
2. Use `StateGraph` from LangGraph
3. Define entry and exit nodes explicitly
4. Use `MemorySaver` or `SqliteSaver` for checkpointing
5. Export a single callable: `run_<graph_name>(state: dict) -> dict`
6. Handle all errors — graphs must never raise uncaught exceptions to the API
7. Log start/end to Supabase via `SupabaseStateLogger`

---

## 4. Tool Contract

Every tool in `tools/` MUST:

1. Be stateless — no global mutable state
2. Fail silently with logging — tools are **non-fatal** by design
3. Log all actions via `structlog`
4. Retry transient network errors via `tenacity`
5. Never raise exceptions to calling agents — return error dicts instead

---

## 5. API Contract

| Endpoint | Method | Purpose |
|---|---|---|
| `/run` | POST | Submit a job |
| `/job/{id}` | GET | Poll job status + output |
| `/jobs` | GET | List recent jobs |
| `/agents` | GET | List all 61 registered agents |
| `/health` | GET | Liveness check |

**Job lifecycle:** `queued` → `running` → `complete` | `failed`

**Sync vs async:** `sync: true` blocks until done. Default is background execution.

---

## 6. Supabase Schema (required tables)

| Table | Purpose |
|---|---|
| `jobs` | Job queue: id, brief, status, output, error, elapsed_seconds |
| `agent_state_log` | PRE/POST checkpoints from every agent call |
| `email_log` | Every Resend send attempt |
| `call_metrics` | Token usage, latency per Claude call |

Schema SQL is in `scripts/create_schema.sql`.

---

## 7. Environment Variables

All secrets live in `.env`. Never hardcode. See `.env.example` for the full list.

Required for core operation:
```
ANTHROPIC_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
RESEND_API_KEY
RESEND_FROM_EMAIL
RESEND_FROM_NAME
```

---

## 8. Deployment

- **GCP VM:** runs `uvicorn api.main:app` as a systemd service
- **Port:** 8000 (internal), proxied via nginx on 443
- **Daemon:** starts automatically on app boot via `lifespan` thread
- **Docker:** `docker-compose up -d` from repo root
- **Logs:** `logs/api.log` — rotated weekly

To deploy a new agent: push to `main` → systemd service auto-restarts on file change (via `--reload` in dev, manual restart in prod).

---

## 9. Forbidden Patterns

- ❌ `build_graph()` inside agent files — graphs live in `graphs/`
- ❌ Human names or nicknames in agent skill files
- ❌ Hardcoded API keys anywhere
- ❌ `print()` for logging — use `structlog`
- ❌ Swallowing exceptions silently without logging
- ❌ Agent files that import from other agent files
- ❌ `from agents import *` in production code
- ❌ Blocking calls inside async route handlers — use `BackgroundTasks`

---

## 10. Adding a New Agent

1. Create `agents/<role>.py` following the Gen 2 spec (see any existing agent)
2. Add import + `__all__` entry to `agents/__init__.py` in the correct batch section
3. Add routing keywords to `graphs/supervisor.py` `ROUTING_RULES`
4. Run `python -m pytest tests/` — all tests must pass
5. Commit with message: `feat(agents): add <role>`

---

*Last updated: 2026-03-11 by Perplexity (Marcus / The Maestro)*
