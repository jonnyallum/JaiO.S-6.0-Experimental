# JaiO.S 6.0 — Quick Start Guide

> 93 LangGraph agents. 24 pipelines. Production-grade AI agency.

## 5-Minute Setup

```bash
# Clone
git clone https://github.com/jonnyallum/JaiO.S-6.0-Experimental.git
cd JaiO.S-6.0-Experimental

# Environment
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and BRAIN_URL

# Option A: Docker (recommended)
docker compose up -d

# Option B: Local
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8765
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health + uptime |
| POST | `/run` | Execute single agent |
| POST | `/pipeline` | Execute multi-agent pipeline |
| POST | `/eval` | LLM-as-judge quality gate |
| GET | `/pipelines` | List all 24 pipeline templates |
| GET | `/catalog` | Full agent catalog with keywords |
| GET | `/stats` | Job completion statistics |

## Run a Single Agent

```bash
curl -X POST http://localhost:8765/run \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"brief": "Write a product launch strategy for an AI SaaS tool"}'
```

## Run a Pipeline (Multi-Agent Chain)

```bash
curl -X POST http://localhost:8765/pipeline \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pipeline": "seo_campaign", "task": "Create SEO strategy for ai-hedge-fund.com"}'
```

## Run Custom Pipeline

```bash
curl -X POST http://localhost:8765/pipeline \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"custom_steps": ["research_analyst", "copywriter", "seo_specialist"], "task": "Write optimized blog post about AI agents"}'
```

## Run Tests

```bash
pytest tests/ -v
```

## Architecture

```
jaios6/
├── agents/         # 93 LangGraph skill nodes (TypedDict state, retries, checkpoints)
├── graphs/         # Supervisor, pipeline engine, eval gate
├── personas/       # Role → personality mapping
├── tools/          # Web search, Supabase logger, code executor
├── api/            # FastAPI server
├── utils/          # Metrics, checkpoints
├── tests/          # Pytest suite
├── config/         # Settings & environment
├── Dockerfile      # Container build
└── docker-compose.yml
```

## Adding a New Agent

1. Create `agents/my_agent.py` following the 19-point spec (see any existing agent)
2. Add routing keywords to `ROUTING_RULES` in `graphs/supervisor.py`
3. Wire the `elif` branch in `execute_single_agent()`
4. Add a test in `tests/test_agents.py`
5. Run `pytest tests/ -v` to verify

## Available Pipelines (24)

- `technical_audit` — security → architecture → code review → QA
- `product_launch` — strategy → orchestrator → copy → social
- `seo_campaign` — research → SEO → copy → content scaling
- `competitor_intel` — monitor → research → BI report
- `sales_blitz` — research → conversion → email → ad copy
- `venture_exploration` — ideation → research → monetisation → pricing
- ... and 18 more (see `/pipelines` endpoint)
