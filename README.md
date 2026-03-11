# Jai.OS 6.0 — LangGraph Rebuild

> **Experimental Branch** | Ground-up rebuild of the JonnyAI agent system on LangGraph | Production-grade agent orchestration

---

## What This Is

**Jai.OS 6.0** is a complete architectural rebuild of the JonnyAI agent system using [LangGraph](https://github.com/langchain-ai/langgraph) for stateful, self-correcting multi-agent workflows.

**Parent repo:** [JaiOS 5.0](https://github.com/jonnyallum/Antigravity_Orchestra)

**Status:** Active build — 50+ agents live, @langraph doctrine applied, supervisor graph in progress

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
│           LangGraph Orchestration Layer                 │
└─────────────────────────────────────────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Supervisor│ │  Agent   │ │  Agent   │
│(@langraph)│ │  Graphs  │ │  Graphs  │
└──────────┘ └──────────┘ └──────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Supabase │ │   MCP    │ │  Tools   │
│ (State)  │ │ Servers  │ │  (n8n)   │
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
├── .agent/skills/langraph/      # @langraph SKILL.md — LangGraph doctrine
├── agents/                      # 50+ LangGraph agent nodes
│   ├── github_intelligence.py
│   ├── security_audit.py
│   ├── architecture_review.py
│   ├── quality_validation.py
│   ├── code_reviewer.py
│   ├── dependency_audit.py
│   ├── brief_writer.py
│   ├── proposal_writer.py
│   ├── social_post_generator.py
│   ├── copywriter.py
│   ├── email_architect.py
│   ├── competitor_monitor.py
│   ├── research_analyst.py
│   ├── business_intelligence.py
│   ├── analytics_reporter.py
│   ├── course_designer.py
│   ├── chatbot_designer.py
│   ├── funnel_architect.py
│   ├── ecommerce_strategist.py
│   ├── persona_builder.py
│   ├── brand_voice_guide.py
│   ├── creative_director.py
│   ├── ui_designer.py
│   ├── legal_advisor.py
│   ├── financial_analyst.py
│   ├── fact_checker.py
│   ├── truth_verifier.py
│   ├── pipeline_monitor.py
│   ├── launch_orchestrator.py
│   ├── project_manager.py
│   ├── customer_success.py
│   ├── ab_test_designer.py
│   ├── ad_copy_writer.py
│   ├── agent_builder.py
│   ├── voice_synthesiser.py
│   ├── venture_ideator.py
│   └── ... (50+ total)
├── graphs/                      # Workflow graphs
│   ├── supervisor.py            # Orchestration layer
│   └── README.md
├── state/                       # State schemas
│   ├── base.py                  # Base state TypedDict
│   └── README.md
├── tools/                       # Custom tools (MCP wrappers)
│   ├── supabase_tools.py
│   ├── github_tools.py
│   ├── social_tools.py
│   ├── notification_tools.py
│   └── README.md
├── config/                      # Configuration
│   ├── settings.py
│   └── .env.example
├── personas/                    # Runtime persona injection
│   └── config.py
├── migrations/                  # DB schema versioning
├── scripts/
├── tests/
└── requirements.txt
```

---

## Agent Clusters (50+ Agents)

| Cluster | Agents |
|---------|--------|
| **Technical Core** | `github_intelligence`, `security_audit`, `architecture_review`, `quality_validation`, `code_reviewer`, `dependency_audit` |
| **Agency Delivery** | `brief_writer`, `proposal_writer`, `social_post_generator`, `copywriter`, `email_architect` |
| **Intelligence** | `competitor_monitor`, `research_analyst`, `business_intelligence`, `analytics_reporter` |
| **Product / Growth** | `course_designer`, `chatbot_designer`, `funnel_architect`, `ecommerce_strategist` |
| **Creative** | `persona_builder`, `brand_voice_guide`, `creative_director`, `ui_designer` |
| **Compliance / Verification** | `legal_advisor`, `financial_analyst`, `fact_checker`, `truth_verifier` |
| **Ops** | `pipeline_monitor`, `launch_orchestrator`, `project_manager`, `customer_success` |
| **Growth / Ads** | `ab_test_designer`, `ad_copy_writer`, `agent_builder`, `voice_synthesiser`, `venture_ideator` |

---

## Phase 1: Foundation

**Goal:** Get LangGraph running, prove it works

**Deliverables:**
1. ✅ Repo structure
2. ✅ LangGraph installed on GCP VM
3. ✅ Supabase state schema
4. ✅ 50+ agents as LangGraph nodes
5. ✅ @langraph doctrine applied across all agents
6. ⏳ Supervisor graph wired and operational

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

**Current phase:** Active build — 50+ agents live, @langraph doctrine applied

**Next milestone:** Supervisor graph fully wired + first production workflow

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
| **Agent count** | ~70 markdown agents | 50+ live Python nodes |

---

## Links

- **Parent repo:** [JaiOS 5.0](https://github.com/jonnyallum/Antigravity_Orchestra)
- **LangGraph docs:** [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
- **Architecture deep dive:** [ARCHITECTURE.md](ARCHITECTURE.md)

---

**Built by:** Jonny Allum + JonnyAI  
**Orchestrated by:** @langraph (LangGraph Systems Architect)  
**Execution:** Claude Code + 50+ specialist agents

*Jai.OS 6.0 — Production-grade agent orchestration for the real world.*
