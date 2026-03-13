# JaiO.S 6.0 — The LangGraph Orchestra

> **93 agents** | **LangGraph + Claude + Supabase** | Production-grade AI agency OS

## Quick Start

```bash
# Health check
curl http://localhost:8765/health

# Submit a job (async)
curl -X POST http://localhost:8765/run \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"brief": "Write a competitive analysis of Vercel vs Netlify"}'

# Submit a job (sync — waits for result)
curl -X POST http://localhost:8765/run \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"brief": "Summarise the latest React 19 features", "sync": true}'
```

## API Endpoints

| Method | Endpoint | Description |
|:-------|:---------|:------------|
| GET | `/health` | Liveness check |
| GET | `/agents` | List all 93 agents |
| GET | `/catalog` | Full agent catalog with routing keywords |
| GET | `/pipelines` | List all pipeline templates |
| GET | `/metrics` | Observability metrics |
| GET | `/jobs` | List recent jobs |
| GET | `/job/{id}` | Get job status + output |
| POST | `/run` | Submit a job |

## Architecture

```
jaios6/
├── agents/       93 Python skill nodes (LangGraph StateGraph)
├── graphs/       supervisor.py (intent → routing → pipeline → execution)
├── personas/     config.py (Pydantic Settings + env-injectable identities)
├── tools/        External integrations (GitHub, Supabase, Resend, web search)
├── tests/        Integration test suite (pytest)
├── api/          FastAPI server
├── state/        Shared TypedDict base state
├── config/       Settings (Pydantic)
└── CATALOG.md    Auto-generated agent catalog
```

## Running Tests

```bash
# Agent load smoke test (no API calls)
cd /home/jonny/antigravity/jaios6
.venv/bin/pytest tests/test_agent_load.py -v

# API integration tests (requires running server)
.venv/bin/pytest tests/test_api_endpoints.py -v

# Routing integrity tests
.venv/bin/pytest tests/test_routing_integrity.py -v

# All tests
.venv/bin/pytest tests/ -v
```

## Agent Catalog

See [CATALOG.md](CATALOG.md) for the full auto-generated agent list.

---

*JaiO.S 6.0 — Built by Antigravity Agency | Jai.OS Architecture*
