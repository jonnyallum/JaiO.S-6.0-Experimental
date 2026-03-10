# Jai.OS 6.0 — LangGraph Rebuild

> **Experimental Branch** | Ground-up rebuild of the JonnyAI agent system on LangGraph | Production-grade agent orchestration

---

## What This Is

**Jai.OS 6.0** is a complete architectural rebuild of the JonnyAI 70-agent system using [LangGraph](https://github.com/langchain-ai/langgraph) for stateful, self-correcting multi-agent workflows.

**Parent repo:** [JaiOS 5.0](https://github.com/jonnyallum/Antigravity_Orchestra)

**Status:** Foundation phase — framework setup + first 5 agents

---

## Why We're Rebuilding

The current stack (Jai.OS 5.0) uses manual Python scripts with while-loops for orchestration. It works, but doesn't scale. LangGraph gives us:

- **State persistence** — workflows resume after crashes
- **Self-correction** — automatic retry logic with fallbacks
- **Conditional routing** — agents route based on real-time conditions
- **Visual debugging** — see workflows as graphs
- **Production-grade** — used by companies at scale

**Cost:** +£40/mo infrastructure (£145-225/mo total vs £105-185/mo current)

**Timeline:** 5 months to full migration

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              LangGraph Orchestration Layer               │
└─────────────────────────────────────────────────────────┘
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
         ┌──────────┐ ┌──────────┐ ┌──────────┐
         │ Supervisor│ │  Agent   │ │  Agent   │
         │  (@marcus)│ │  Graphs  │ │  Graphs  │
         └──────────┘ └──────────┘ └──────────┘
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
         ┌──────────┐ ┌──────────┐ ┌──────────┐
         │ Supabase │ │   MCP    │ │  Tools   │
         │  (State) │ │  Servers │ │  (n8n)   │
         └──────────┘ └──────────┘ └──────────┘
```

**Key components:**
- **LangGraph** — orchestration engine (stateful workflows as graphs)
- **Supabase** — state persistence + agent memory
- **MCP servers** — tool integrations (GitHub, Supabase, Brave Search, etc.)
- **n8n** — email automation, social media, client workflows
- **GCP VM** — execution environment (current: e2-medium 4GB RAM)

---

## Repo Structure

```
JaiO.S-6.0-Experimental/
├── README.md                    # This file
├── ARCHITECTURE.md              # System design deep dive
├── MIGRATION.md                 # 5.0 → 6.0 migration plan
├── agents/                      # LangGraph agent nodes
│   ├── github_intelligence.py   # GitHub intelligence
│   ├── data_extraction.py       # Data extraction
│   ├── quality_validation.py    # Quality gates
│   ├── security_audit.py        # Security audits
│   ├── architecture_review.py   # Full-stack architect
│   ├── dependency_audit.py      # Dependency analysis
│   ├── code_reviewer.py         # File-level code review
│   ├── social_post_generator.py # FB/IG content
│   ├── brief_writer.py          # Client proposals & briefs
│   ├── supabase_intelligence.py # Shared Brain queries
│   └── README.md                # Agent authoring guide
├── graphs/                      # Workflow graphs
│   ├── supervisor.py            # @marcus orchestration
│   └── README.md                # Graph design patterns
├── state/                       # State schemas
│   ├── base.py                  # Base state TypedDict
│   └── README.md                # State design guide
├── tools/                       # Custom tools (MCP wrappers)
│   ├── supabase_tools.py        # Shared Brain queries
│   ├── github_tools.py          # Repo operations
│   ├── social_tools.py          # Meta FB/IG publishing
│   ├── notification_tools.py    # Telegram alerts
│   └── README.md                # Tool development guide
├── config/                      # Configuration
│   ├── settings.py              # Pydantic settings
│   └── .env.example             # Environment variable template
├── personas/                    # Runtime persona injection
│   └── config.py                # get_persona() — identity injected at runtime
└── requirements.txt             # Python dependencies
```

---

## Phase 1: Foundation (Week 1-2)

**Goal:** Get LangGraph running, prove it works

**Deliverables:**
1. ✅ Repo structure
2. ✅ LangGraph installed on GCP VM
3. ✅ Supabase state schema
4. ✅ First 10 agents as LangGraph nodes
5. ⏳ One working graph: BL Motorcycles order processing

**First 10 agents:**
- **github_intelligence** — GitHub repo analysis
- **security_audit** — repo security review
- **architecture_review** — repo architecture assessment
- **data_extraction** — structured data parsing
- **quality_validation** — output quality gate
- **dependency_audit** — package vulnerability/staleness scan
- **code_reviewer** — file-level code review
- **social_post_generator** — FB/IG copy + publish
- **brief_writer** — client proposals & SOWs
- **supabase_intelligence** — Shared Brain status reports

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Orchestration** | LangGraph (Python) |
| **State** | Supabase (PostgreSQL) |
| **AI Models** | Claude Sonnet 4.6 |
| **Tools** | MCP servers (GitHub, Supabase, Brave, etc.) |
| **Deployment** | GCP VM (e2-medium, upgradeable to e2-standard-2) |
| **Monitoring** | LangSmith (optional), custom dashboards |

---

## Quick Start (For Developers)

```bash
# Clone repo
git clone https://github.com/jonnyallum/JaiO.S-6.0-Experimental.git
cd JaiO.S-6.0-Experimental

# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in credentials
cp .env.example .env

# Run Phase 1 test
PYTHONPATH=. python graphs/test_graph.py

# Run tests
pytest tests/
```

---

## Contributing Agents

Every agent follows the **@langraph doctrine**:

```python
# Contract docstring → named constants → TypedDict state →
# pure collection phase → @retry Claude phase →
# PRE checkpoint → POST checkpoint → discriminated error blocks

def my_skill_node(state: MySkillState) -> dict:
    ...
```

See [agents/README.md](agents/README.md) for the full guide.

---

## Status

**Current phase:** Foundation — 10 agents live, @langraph doctrine applied  
**Next milestone:** Supervisor graph + BL Motorcycles order processing workflow

---

## Key Differences vs Jai.OS 5.0

| Feature | 5.0 (Current) | 6.0 (This Repo) |
|---------|---------------|--------------------|
| **Orchestration** | Python while-loops | LangGraph state machines |
| **State** | Manual JSON writes | Automatic checkpointing |
| **Routing** | Hardcoded if/else | Conditional edges |
| **Retry logic** | Manual try/catch | Built-in circuit breakers |
| **Debugging** | Print statements | Visual graph debugging |
| **Agent handoffs** | NEXTHOP comments | Graph edges |
| **Memory** | Supabase tables (manual) | LangGraph state (automatic) |

---

## Links

- **Parent repo:** [JaiOS 5.0](https://github.com/jonnyallum/Antigravity_Orchestra)
- **LangGraph docs:** [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
- **Architecture deep dive:** [ARCHITECTURE.md](ARCHITECTURE.md)

---

**Built by:** Jonny Allum + JonnyAI  
**Orchestrated by:** @Marcus (The Maestro)  
**Execution:** Claude Code + 70 specialist agents

*Jai.OS 6.0 — Production-grade agent orchestration for the real world.*
