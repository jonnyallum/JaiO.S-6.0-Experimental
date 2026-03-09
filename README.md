# Jai.OS 6.0 — LangGraph Rebuild

> **Experimental Branch** | Ground-up rebuild of the Antigravity Orchestra on LangGraph | Production-grade agent orchestration

---

## What This Is

**Jai.OS 6.0** is a complete architectural rebuild of the 70-agent Antigravity Orchestra using [LangGraph](https://github.com/langchain-ai/langgraph) for stateful, self-correcting multi-agent workflows.

**Parent repo:** [Antigravity_Orchestra](https://github.com/jonnyallum/Antigravity_Orchestra)

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
│   ├── hugo.py                  # GitHub intelligence
│   ├── parser.py                # Data extraction
│   ├── qualityguard.py          # Quality gates
│   ├── sam.py                   # Security audits
│   ├── sebastian.py             # Full-stack architect
│   └── README.md                # Agent authoring guide
├── graphs/                      # Workflow graphs
│   ├── supervisor.py            # @marcus orchestration
│   ├── bl_motorcycles.py        # BL order processing
│   └── README.md                # Graph design patterns
├── state/                       # State schemas
│   ├── base.py                  # Base state TypedDict
│   ├── order_state.py           # BL Motorcycles order
│   └── README.md                # State design guide
├── tools/                       # Custom tools (MCP wrappers)
│   ├── supabase_tools.py        # Shared Brain queries
│   ├── github_tools.py          # Repo operations
│   └── README.md                # Tool development guide
├── config/                      # Configuration
│   ├── langgraph.json           # LangGraph config
│   ├── supabase.json            # DB connection
│   └── mcp_servers.json         # MCP server registry
├── tests/                       # Test suite
│   ├── test_agents.py           # Agent node tests
│   ├── test_graphs.py           # Graph execution tests
│   └── test_state.py            # State persistence tests
├── scripts/                     # Utility scripts
│   ├── deploy.sh                # Deploy to GCP VM
│   ├── migrate_agent.py         # 5.0 → 6.0 agent converter
│   └── validate.py              # Schema validation
├── docs/                        # Documentation
│   ├── GETTING_STARTED.md       # Quick start guide
│   ├── AGENT_AUTHORING.md       # How to build agents
│   ├── GRAPH_PATTERNS.md        # Common workflow patterns
│   └── COST_ANALYSIS.md         # Infrastructure costs
└── requirements.txt             # Python dependencies
```

---

## Phase 1: Foundation (Week 1-2)

**Goal:** Get LangGraph running, prove it works

**Deliverables:**
1. ✅ Repo structure
2. ⏳ LangGraph installed on GCP VM
3. ⏳ Supabase state schema
4. ⏳ First 5 agents as LangGraph nodes
5. ⏳ One working graph: BL Motorcycles order processing

**First 5 agents:**
- **@hugo** — GitHub intelligence (high usage)
- **@parser** — data extraction (BL blocker)
- **@qualityguard** — quality gates (production safety)
- **@sam** — security audits (production safety)
- **@sebastian** — full-stack architect (builds everything)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Orchestration** | LangGraph (Python) |
| **State** | Supabase (PostgreSQL) |
| **AI Models** | Claude Sonnet 4, GPT-4, DeepSeek R1 |
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

# Configure Supabase connection
cp config/supabase.example.json config/supabase.json
# Edit config/supabase.json with your credentials

# Run first test workflow
python graphs/bl_motorcycles.py

# Run tests
pytest tests/
```

---

## Contributing Agents

See [docs/AGENT_AUTHORING.md](docs/AGENT_AUTHORING.md) for the full guide.

**Quick example:**

```python
from typing_extensions import TypedDict
from langgraph.graph import StateGraph

class AgentState(TypedDict):
    input: str
    output: str

def agent_node(state: AgentState) -> dict:
    # Your agent logic here
    result = process(state["input"])
    return {"output": result}

# Add to graph
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
```

---

## Status

**Current phase:** Foundation setup  
**Completion:** 10% (repo structure done, code in progress)  
**Next milestone:** First graph running on GCP VM (Week 2)

---

## Key Differences vs Jai.OS 5.0

| Feature | 5.0 (Current) | 6.0 (This Repo) |
|---------|---------------|-----------------|
| **Orchestration** | Python while-loops | LangGraph state machines |
| **State** | Manual JSON writes | Automatic checkpointing |
| **Routing** | Hardcoded if/else | Conditional edges |
| **Retry logic** | Manual try/catch | Built-in circuit breakers |
| **Debugging** | Print statements | Visual graph debugging |
| **Agent handoffs** | NEXTHOP comments | Graph edges |
| **Memory** | Supabase tables (manual) | LangGraph state (automatic) |

---

## Links

- **Parent repo:** [Antigravity_Orchestra](https://github.com/jonnyallum/Antigravity_Orchestra)
- **LangGraph docs:** [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
- **Architecture deep dive:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Migration plan:** [MIGRATION.md](MIGRATION.md)
- **Cost analysis:** [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md)

---

**Built by:** Jonny Allum + The Antigravity Orchestra  
**Orchestrated by:** @Marcus Cole (The Maestro)  
**Research:** Perplexity AI  
**Execution:** Claude Code + 70 specialist agents

*Jai.OS 6.0 — Production-grade agent orchestration for the real world.*
