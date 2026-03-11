# JaiO.S 6.0 — Operating Rules

> **This is the system contract for JaiO.S 6.0.**
> Claude Code reads this file in full before every session.
> No guessing. No assumptions. Everything is defined here.
> When in doubt: check this file, then check the code, then ask.

---

## Session Start Protocol

**Run at the start of every Claude Code session:**

```bash
# 1. Pull latest
git pull origin main

# 2. Verify all agents pass the 19-point spec
python -m pytest tests/ -v

# 3. Check API is healthy (if running locally)
curl http://localhost:8000/health

# 4. Check Supabase job queue
python -m api.main list
```

If tests fail: **stop. fix the failure. do not build on broken ground.**

---

## System Map

```
┌─────────────────────────────────────────────────────────────────┐
│  INPUTS                                                         │
│  POST /run  │  Telegram Bot  │  n8n Webhook  │  Daemon (30s)   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  api/main.py  (FastAPI)                                         │
│  • Validates request                                            │
│  • Creates job record in Supabase (status: queued)              │
│  • Dispatches to background or sync execution                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  graphs/supervisor.py  (LangGraph StateGraph)                   │
│  • Reads request intent                                         │
│  • Routes to the correct specialised graph                      │
│  • Composes final output from agent outputs                     │
└──────┬──────────────┬───────────────┬───────────────────────────┘
       │              │               │
       ▼              ▼               ▼
  graphs/         graphs/         graphs/
  project_        bl_             [client].py
  health.py       motorcycles.py  (one per client)
       │              │               │
       └──────────────┴───────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│  agents/<skill>.py  (LangGraph nodes)                           │
│  • Loads persona from personas/config.py                        │
│  • Builds prompt (pure function)                                │
│  • Calls Claude via Anthropic SDK                               │
│  • Writes PRE / POST checkpoints to Supabase                    │
│  • Returns typed output dict                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   Supabase          Telegram           Resend
   (state log,       (errors only)      (email delivery)
    job queue,
    email_log,
    call_metrics)
```

**Rule: Every job enters via `api/main.py`. Nothing bypasses the API. Ever.**

---

## Directory Structure

```
JaiO.S-6.0-Experimental/
├── api/
│   ├── __init__.py
│   └── main.py              ← FastAPI entry point + daemon
├── agents/
│   ├── __init__.py          ← imports all 61 nodes + __all__
│   └── <role>.py            ← one file per agent (Gen 2 spec)
├── graphs/
│   ├── supervisor.py        ← routes requests to client graphs
│   ├── project_health.py    ← project health workflow
│   └── <client>.py          ← one graph per client
├── tools/
│   ├── notification_tools.py  ← Telegram alerts
│   ├── resend_tools.py        ← email delivery
│   ├── supabase_tools.py      ← state logging
│   ├── telemetry.py           ← call metrics
│   ├── social_tools.py        ← Meta Graph API
│   └── github_tools.py        ← GitHub operations
├── personas/
│   └── config.py            ← all 61 personas, get_persona(role)
├── state/
│   └── base.py              ← BaseState TypedDict
├── config/
│   └── settings.py          ← pydantic-settings, reads .env
├── scripts/
│   ├── create_schema.sql    ← Supabase schema
│   └── setup_vm.sh          ← GCP VM bootstrap
├── migrations/              ← incremental DB migrations
├── tests/                   ← pytest suite
├── docs/                    ← extended documentation
├── logs/                    ← runtime logs (gitignored)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                     ← secrets (gitignored)
├── .env.example             ← template (committed)
├── OPERATING_RULES.md       ← THIS FILE
└── README.md
```

---

## 1. Agent Contract (Gen 2 Spec — 19 Points)

Every file in `agents/` must comply with all 19 points. Non-compliance blocks merge.

### Structure

```python
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : <role>
SKILL : One-line description of what this agent does

Node Contract (@langraph doctrine):
  Inputs   : field (type), field (type)  — immutable after entry
  Outputs  : field (type), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All state fields are thread-scoped only.

Loop Policy:
  NONE — single-pass node. One Claude call per invocation.

Failure Discrimination:
  PERMANENT  → ValueError (bad inputs — do not retry)
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError (retry via tenacity)
  UNEXPECTED → Exception (log + alert)

Checkpoint Semantics:
  PRE  — written immediately before Claude call
  POST — written after successful completion

Persona injected at runtime via personas/config.py — no identity in this file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
```

### Required Imports (exactly these, in this order)

```python
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
```

### Required Constants

```python
ROLE        = "<role>"          # matches filename and persona key
MAX_RETRIES = 3
RETRY_MIN_S = 3
RETRY_MAX_S = 45
MAX_TOKENS  = <int>             # set per agent; never hardcode inline
```

### Required State Class

```python
class <Role>State(BaseState):
    # Inputs — written by caller, immutable inside this node
    <input_field>: <type>
    # Outputs — written by this node
    <output_field>: <type>
```

### Required Functions

| Function | Signature | Rules |
|---|---|---|
| `_build_prompt` | `(state, persona) -> str` | Pure — zero I/O, no side effects |
| `_generate` | `(client, prompt, metrics) -> str` | Must be decorated with `@retry` |
| `<role>_node` | `(state: <Role>State) -> dict` | The LangGraph node. One PRE + one POST checkpoint. |

### `_generate` decorator (mandatory)

```python
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()
```

### Node Return Contract

**On success:**
```python
return {"<output_key>": result, "error": None, "workflow_id": thread_id, "agent": ROLE}
```

**On any failure:**
```python
return {"<output_key>": <empty_default>, "error": msg, "workflow_id": thread_id, "agent": ROLE}
```

### Checkpoint Pattern (mandatory)

```python
def _checkpoint(cid: str, payload: dict) -> None:
    state_logger.log_state(thread_id, cid, ROLE, payload)

# Before Claude call:
_checkpoint(f"{ROLE}_pre_{ts}", {"input_summary": ..., "status": "generating"})

# After Claude call:
_checkpoint(f"{ROLE}_post_{ts}", {"status": "completed", "output_chars": len(result)})

# On error:
_checkpoint(f"{ROLE}_err_{ts}", {"status": "failed", "error": msg})
```

### Exception Handling Pattern (mandatory)

```python
except ValueError as exc:
    # PERMANENT — bad inputs, do not retry
    ...notify + checkpoint + return error dict

except anthropic.APIError as exc:
    # TRANSIENT — already retried by tenacity; still failed
    ...notify + return error dict

except Exception as exc:
    # UNEXPECTED — log full traceback
    ...log.exception + notify + return error dict
```

### The 19 Rules (checklist)

1. ✅ Docstring header with all 6 sections
2. ✅ All 6 required imports present
3. ✅ `ROLE`, `MAX_RETRIES`, `RETRY_MIN_S`, `RETRY_MAX_S`, `MAX_TOKENS` constants defined
4. ✅ State class extends `BaseState`
5. ✅ `_build_prompt` is pure (no I/O, no side effects)
6. ✅ `_build_prompt` aliased as `_build_prompt = _build_variants_prompt` if named differently
7. ✅ `_generate` has `@retry` decorator with correct exception types
8. ✅ `_generate` calls `metrics.start()` and `metrics.record(response)`
9. ✅ `model="claude-sonnet-4-6"` — no other model unless explicitly approved
10. ✅ `MAX_TOKENS` used — never a literal integer in `messages.create()`
11. ✅ PRE checkpoint written before Claude call
12. ✅ POST checkpoint written after success
13. ✅ ERR checkpoint written on every failure path
14. ✅ `TelegramNotifier.agent_error()` called on every failure
15. ✅ Three exception classes handled: `ValueError`, `anthropic.APIError`, `Exception`
16. ✅ Return dict includes `workflow_id`, `agent`, `error` on every path
17. ✅ No `build_graph()` in this file
18. ✅ No human names, nicknames, or identity in agent logic
19. ✅ No hardcoded secrets or API keys

---

## 2. Graph Contract

Every file in `graphs/` must follow these rules.

### Structure

```python
"""
GRAPH: <name>
PURPOSE: What this graph does — one sentence
AGENTS: list of agent roles used
CLIENT: which client this serves (or "system" for internal)
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agents.<role_a> import <role_a>_node
from agents.<role_b> import <role_b>_node
from state.base import BaseState
from tools.supabase_tools import SupabaseStateLogger

def run_<graph_name>(state: dict) -> dict:
    """
    Entry point. Called by graphs/supervisor.py or api/main.py directly.
    Must never raise — catch all exceptions and return error dict.
    """
```

### Rules

1. Import only node functions from `agents/` — never agent internals
2. Export exactly one callable: `run_<graph_name>(state: dict) -> dict`
3. Use `StateGraph` — no custom execution loops
4. Define `START` and `END` explicitly — no implicit termination
5. Use `MemorySaver` for checkpointing (upgrade to `SqliteSaver` for persistence)
6. Log graph start and end to Supabase via `SupabaseStateLogger`
7. Must never raise uncaught exceptions to the API layer
8. One graph per client — named `graphs/<client_slug>.py`

### Supervisor Contract

`graphs/supervisor.py` is the single routing layer between the API and all client graphs.

```python
# ROUTING_RULES maps intent keywords → graph callable
ROUTING_RULES = {
    "bl_motorcycles": run_bl_motorcycles,
    "marzer":         run_marzer,
    "sparta":         run_sparta,
    ...
}

def run_supervisor(state: dict) -> dict:
    """Route to the correct graph based on state['request'] content."""
```

The supervisor must have a fallback for unrecognised intents — log and return a clear error dict.

---

## 3. Tool Contract

Every file in `tools/` must follow these rules.

| Rule | Detail |
|---|---|
| Stateless | No module-level mutable state |
| Non-fatal | Tools never raise to agents — return `{"error": msg}` dicts |
| Logged | Every action logged via `structlog` |
| Retried | Network calls use `tenacity` with exponential backoff |
| Typed | All public methods have type hints |

### Tool Inventory

| File | Class | Purpose |
|---|---|---|
| `supabase_tools.py` | `SupabaseStateLogger` | PRE/POST state checkpoints |
| `telemetry.py` | `CallMetrics` | Token usage + latency per call |
| `notification_tools.py` | `TelegramNotifier` | Error alerts to Telegram |
| `resend_tools.py` | `ResendMailer` | Email delivery via Resend API |
| `social_tools.py` | `SocialPublisher` | Meta Graph API posting |
| `github_tools.py` | `GitHubTools` | Repo operations |

---

## 4. API Contract

### Endpoints

| Method | Endpoint | Body / Params | Returns |
|---|---|---|---|
| `POST` | `/run` | `{brief, submitted_by?, sync?}` | `{job_id, status, check_status}` |
| `GET` | `/job/{id}` | — | Full job record + output |
| `GET` | `/jobs` | `?limit=20&status=queued` | List of recent jobs |
| `GET` | `/agents` | — | All 61 agent node names |
| `GET` | `/health` | — | Version, uptime, key status |

### Job Lifecycle

```
queued → running → complete
                 ↘ failed
```

- `queued`: created in Supabase, not yet executing
- `running`: supervisor and agents are executing
- `complete`: output written to `jobs.output`
- `failed`: error written to `jobs.error`, Telegram alert sent

### sync vs async

```json
// Background (default) — returns immediately
{"brief": "Write a landing page for BL Motorcycles", "sync": false}

// Blocking — waits for completion (use for simple, fast jobs only)
{"brief": "Summarise this brief in 3 bullets", "sync": true}
```

---

## 5. State Contract

### BaseState

All agent state classes extend `BaseState` from `state/base.py`:

```python
class BaseState(TypedDict, total=False):
    workflow_id: str        # UUID — set by API, propagated through all nodes
    agent:       str        # Role of the last agent to write to state
    error:       str | None # None on success, error message on failure
    created_at:  str        # ISO 8601 timestamp
```

### Rules

- State is **immutable within a node** — inputs are read-only once the node starts
- State is **thread-scoped** — no cross-job state bleed
- `workflow_id` must be present on every state dict entering the system
- Agents append to state — they never delete or overwrite input fields

---

## 6. Supabase Schema

All tables are defined in `scripts/create_schema.sql`. Run this once on a new Supabase project.

### Required Tables

| Table | Key Columns | Purpose |
|---|---|---|
| `jobs` | `id, brief, status, output, error, elapsed_seconds, submitted_by` | Job queue + results |
| `agent_state_log` | `id, workflow_id, checkpoint_id, role, payload, created_at` | PRE/POST checkpoints |
| `email_log` | `id, resend_id, recipients, subject, status, error, tags, sent_at` | All email send attempts |
| `call_metrics` | `id, workflow_id, role, model, input_tokens, output_tokens, latency_ms` | Token usage + cost tracking |

### Writing to Supabase

- Use `SupabaseStateLogger` for checkpoints — never raw HTTP calls from agent files
- Use `ResendMailer._log_to_supabase()` for email logs — built into the tool
- Use `CallMetrics.persist()` for token metrics — built into the tool
- Direct Supabase writes from agents are **forbidden** except via these tools

---

## 7. Personas Contract

All 61 agent personas live in `personas/config.py`.

```python
from personas.config import get_persona

persona = get_persona(ROLE)
# persona = {
#     "role":        "content_scaler",
#     "personality": "You are a ...",
#     "tone":        "...",
#     "expertise":   [...],
# }
```

### Rules

- Agent skill files contain **zero identity** — no personality, no tone, no background
- All identity is injected via `get_persona(ROLE)` at runtime
- `personas/config.py` is the single source of truth for all 61 identities
- Persona keys must match `ROLE` constants in agent files exactly
- No human names, nicknames, or pop culture references in agent skill files

---

## 8. Environment Variables

All secrets live in `.env`. Never hardcode. See `.env.example` for every key.

### Required for Core Operation

```bash
# AI
ANTHROPIC_API_KEY=

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Email
RESEND_API_KEY=
RESEND_FROM_EMAIL=hello@jonnyai.co.uk
RESEND_FROM_NAME=JonnyAI
```

### Optional (enables additional capabilities)

```bash
# Fallback LLMs
OPENAI_API_KEY=
GEMINI_API_KEY=

# Social
META_ACCESS_TOKEN=
META_PAGE_ID=

# Voice
ELEVENLABS_API_KEY=

# GitHub
GITHUB_TOKEN=
```

---

## 9. Deployment

### GCP VM (Production)

- **VM:** `35.230.148.83`
- **Service:** `systemd` runs `uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1`
- **Proxy:** nginx on port 443, forwards to 8000
- **Logs:** `logs/api.log` — view with `journalctl -u jaios -f`
- **Restart:** `sudo systemctl restart jaios`
- **Deploy:** push to `main` → SSH to VM → `git pull && sudo systemctl restart jaios`

### Docker (Local / Staging)

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f jaios

# Rebuild after code changes
docker-compose up -d --build

# Stop
docker-compose down
```

### Systemd Service File

Located at `server/systemd/`. Copy to `/etc/systemd/system/jaios.service` on the VM.

---

## 10. Error Taxonomy

Every error in the system is one of three types. Handle accordingly.

| Type | Cause | Action | Retry? |
|---|---|---|---|
| `PERMANENT` | `ValueError` — bad inputs, missing required fields | Log + alert + return error dict | ❌ Never |
| `TRANSIENT` | `APIConnectionError`, `RateLimitError`, `APITimeoutError` | Tenacity retries 3x with backoff | ✅ Auto |
| `UNEXPECTED` | `Exception` — anything else | `log.exception` + alert + return error dict | ❌ Never |

**Telegram alert fires on every failure.** Use `TelegramNotifier.agent_error(ROLE, input_summary, error_msg)`.

---

## 11. Forbidden Patterns

These are hard rules. Breaking them causes silent failures, security issues, or undebuggable state.

```
❌ build_graph() inside agent files — graphs live in graphs/
❌ Human names, nicknames, or identity in agent skill files
❌ Hardcoded API keys, tokens, or secrets anywhere in code
❌ print() for logging — use structlog exclusively
❌ Swallowing exceptions without logging (bare except: pass)
❌ Agent files importing from other agent files
❌ Direct Supabase HTTP calls from agent files — use tools/
❌ Blocking I/O inside async FastAPI route handlers — use BackgroundTasks
❌ Mutable module-level state in tool files
❌ Hardcoded model names inline — use constants
❌ Literal integers for max_tokens in messages.create() — use MAX_TOKENS
❌ from agents import * in any production file
❌ State mutation after node entry (inputs are read-only)
❌ Graph files that raise uncaught exceptions to the API
```

---

## 12. Adding a New Agent — Checklist

Follow this exact sequence. Do not skip steps.

```
□ 1. Create agents/<role>.py using an existing agent as template
□ 2. Complete all 19 points of the Gen 2 spec (see Section 1)
□ 3. Add persona entry to personas/config.py
□ 4. Add import and __all__ entry to agents/__init__.py
□ 5. Add routing keywords to graphs/supervisor.py ROUTING_RULES
□ 6. Write at least one test in tests/test_agents.py
□ 7. Run python -m pytest tests/ — all tests must pass
□ 8. Commit: feat(agents): add <role>
```

---

## 13. Adding a New Client Graph — Checklist

```
□ 1. Create graphs/<client_slug>.py
□ 2. Define run_<client_slug>(state: dict) -> dict
□ 3. Wire the correct agent nodes for this client's workflow
□ 4. Register in graphs/supervisor.py ROUTING_RULES
□ 5. Add client context to personas/config.py if needed
□ 6. Test end-to-end: python -m api.main submit "test brief for <client>"
□ 7. Commit: feat(graphs): add <client_slug> graph
```

---

## 14. Claude Code Directives

These rules apply specifically to Claude Code operating in this repo.

1. **Read this file first.** Every session, without exception.
2. **Check what exists before building.** List the directory. Read the file. Never assume.
3. **Follow the Gen 2 spec.** Every agent, no exceptions. Run the 19-point checklist.
4. **No identity in agent files.** Personas come from `personas/config.py`.
5. **One commit per logical change.** Use conventional commit format: `feat/fix/docs/refactor/test(scope): description`
6. **Tests before commit.** `python -m pytest tests/` must pass.
7. **Use existing tools.** Don't rebuild what's in `tools/`. Extend them if needed.
8. **Log, don't print.** `structlog` everywhere. Never `print()`.
9. **Graphs own workflow logic.** Agents are single-purpose nodes. Routing lives in `graphs/`.
10. **If something is unclear, check ARCHITECTURE.md.** If still unclear, stop and ask.

---

## 15. Quick Reference

```bash
# Run API locally
uvicorn api.main:app --reload --port 8000

# Submit a test job (sync)
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"brief": "Write 3 bullet points about JonnyAI", "sync": true}'

# Check job status
curl http://localhost:8000/job/<job_id>

# List all agents
curl http://localhost:8000/agents

# Run tests
python -m pytest tests/ -v

# Check health
curl http://localhost:8000/health
```

---

*JaiO.S 6.0 — Operating Rules v2.0*
*Last updated: 2026-03-11 by Perplexity*
*Next review: when a new system layer is added*
