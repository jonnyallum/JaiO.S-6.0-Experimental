# Jai.OS 6.0 Architecture

> **Deep dive into the technical architecture** | LangGraph orchestration | State management | Agent design patterns

---

## System Overview

Jai.OS 6.0 is a **stateful multi-agent orchestration system** built on LangGraph. It replaces manual Python scripts with graph-based workflows that support:

- Automatic state persistence and checkpointing
- Self-correcting agents with retry logic
- Conditional routing based on runtime conditions
- Visual debugging and workflow inspection
- Production-grade error handling

---

## Core Architecture Layers

### Layer 1: LangGraph Orchestration

**What it is:** The control plane. All workflows are represented as directed graphs with typed state.

**Components:**
- **StateGraph** — defines nodes (agents) and edges (routing)
- **TypedDict state** — strongly-typed workflow state
- **Conditional edges** — dynamic routing based on state
- **Checkpointing** — automatic state snapshots for resume-after-crash

**Example:**

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class WorkflowState(TypedDict):
    task: str
    result: str
    quality_passed: bool

def agent_a(state: WorkflowState) -> dict:
    return {"result": process(state["task"])}

def agent_b(state: WorkflowState) -> dict:
    return {"quality_passed": validate(state["result"])}

def route_quality(state: WorkflowState) -> str:
    return "end" if state["quality_passed"] else "retry"

graph = StateGraph(WorkflowState)
graph.add_node("agent_a", agent_a)
graph.add_node("agent_b", agent_b)
graph.add_edge(START, "agent_a")
graph.add_edge("agent_a", "agent_b")
graph.add_conditional_edges("agent_b", route_quality, {"end": END, "retry": "agent_a"})

app = graph.compile()
```

---

### Layer 2: State Management (Supabase)

**What it is:** PostgreSQL database storing workflow state, agent memory, learnings, and chatroom history.

**Tables:**

| Table | Purpose | Schema |
|-------|---------|--------|
| `graph_state` | LangGraph checkpoints | `{workflow_id, state_json, timestamp}` |
| `agents` | Agent profiles (70 agents) | `{handle, name, philosophy, tier}` |
| `learnings` | Post-task insights | `{agent, learning, timestamp}` |
| `chatroom` | Session broadcasts | `{agent, message, timestamp}` |
| `projects` | Client project registry | `{name, client, status, metadata}` |

**State persistence flow:**

```
LangGraph node executes
    ↓
State update returned
    ↓
LangGraph writes to Supabase `graph_state`
    ↓
Checkpoint created (automatic)
    ↓
If crash occurs, resume from last checkpoint
```

**Configuration:**

```json
// config/supabase.json
{
  "url": "https://lkwydqtfbdjhxaarelaz.supabase.co",
  "anon_key": "<SUPABASE_ANON_KEY>",
  "state_table": "graph_state",
  "checkpoint_interval": "per_node"
}
```

---

### Layer 3: Agent Nodes

**What they are:** Python functions that receive state and return updates. Each agent is a LangGraph node.

**Agent design pattern:**

```python
# agents/hugo.py
from typing_extensions import TypedDict
import anthropic

class HugoState(TypedDict):
    repo: str
    query: str
    intelligence: str

def hugo_node(state: HugoState) -> dict:
    """
    @hugo — GitHub Intelligence Specialist
    Analyzes repos, PRs, issues, commits.
    """
    client = anthropic.Anthropic()
    
    # Call GitHub MCP server (via tools)
    repo_data = get_repo_contents(state["repo"])
    
    # Claude analyzes
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{
            "role": "user",
            "content": f"Analyze this repo: {repo_data}\n\nQuery: {state['query']}"
        }]
    )
    
    return {"intelligence": response.content[0].text}
```

**Agent authoring rules:**

1. **Pure functions** — no side effects in state logic
2. **Return dict** — keys must match state TypedDict fields
3. **Error handling** — raise exceptions, LangGraph catches them
4. **Tool calls** — use MCP servers for external operations
5. **Logging** — use structured logging, not print statements

---

### Layer 4: MCP Tools

**What they are:** Model Context Protocol servers providing tools to agents.

**Active MCP servers:**

| Server | Tools | Usage |
|--------|-------|-------|
| `github` | Repo ops, PRs, issues | @hugo, @sebastian |
| `supabase` | DB queries, writes | All agents (state + memory) |
| `brave-search` | Web research | @scholar, @intelhub, @sophie |
| `playwright` | Browser automation | @parser, @qualityguard |
| `context7` | Live docs | @sebastian, @neo |
| `figma` | Design files | @priya, @blaise |
| `desktop-commander` | File system, shell | @derek, @owen |

**MCP wrapper pattern:**

```python
# tools/github_tools.py
from mcp import Client

class GitHubTools:
    def __init__(self):
        self.client = Client("github_mcp_direct")
    
    def get_repo_contents(self, owner: str, repo: str, path: str) -> dict:
        return self.client.call(
            "get_file_contents",
            owner=owner,
            repo=repo,
            path=path,
            ref="main"
        )
    
    def create_pr(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> dict:
        return self.client.call(
            "create_pull_request",
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base
        )
```

---

### Layer 5: Graphs (Workflows)

**What they are:** Multi-agent workflows composed as LangGraph graphs.

**Example: BL Motorcycles order processing**

```python
# graphs/bl_motorcycles.py
from langgraph.graph import StateGraph, START, END
from agents import hugo, parser, qualityguard, steve
from state import OrderState

def build_bl_order_graph():
    graph = StateGraph(OrderState)
    
    # Add agents as nodes
    graph.add_node("fetch_order", hugo.fetch_ebay_order)
    graph.add_node("extract_fitment", parser.parse_product_data)
    graph.add_node("quality_check", qualityguard.validate_order)
    graph.add_node("sync_to_crm", steve.write_to_twenty)
    graph.add_node("escalate", marcus.notify_jonny)
    
    # Define flow
    graph.add_edge(START, "fetch_order")
    graph.add_edge("fetch_order", "extract_fitment")
    graph.add_edge("extract_fitment", "quality_check")
    
    # Conditional routing
    def route_quality(state: OrderState) -> str:
        return "sync_to_crm" if state["quality_passed"] else "escalate"
    
    graph.add_conditional_edges(
        "quality_check",
        route_quality,
        {"sync_to_crm": "sync_to_crm", "escalate": "escalate"}
    )
    
    graph.add_edge("sync_to_crm", END)
    graph.add_edge("escalate", END)
    
    return graph.compile()

if __name__ == "__main__":
    app = build_bl_order_graph()
    result = app.invoke({"order_id": "12345"})
    print(result)
```

---

## Supervisor Pattern (@marcus)

The supervisor agent routes high-level tasks to specialist agents.

**Architecture:**

```python
# graphs/supervisor.py
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class SupervisorState(TypedDict):
    task: str
    agent: str
    result: str

def marcus_route(state: SupervisorState) -> dict:
    """
    @marcus — Orchestrator
    Routes tasks to specialist agents based on task type.
    """
    task_type = classify_task(state["task"])
    
    routing_map = {
        "github": "hugo",
        "data_extraction": "parser",
        "quality_audit": "qualityguard",
        "security": "sam",
        "deployment": "owen"
    }
    
    return {"agent": routing_map.get(task_type, "sebastian")}

def execute_agent(state: SupervisorState) -> dict:
    # Dynamically call the selected agent
    agent_map = {
        "hugo": hugo_node,
        "parser": parser_node,
        "qualityguard": qualityguard_node,
        "sam": sam_node,
        "sebastian": sebastian_node
    }
    
    agent_fn = agent_map[state["agent"]]
    result = agent_fn(state)
    return {"result": result}

graph = StateGraph(SupervisorState)
graph.add_node("route", marcus_route)
graph.add_node("execute", execute_agent)
graph.add_edge(START, "route")
graph.add_edge("route", "execute")
graph.add_edge("execute", END)

supervisor = graph.compile()
```

---

## Quality Gate Chain

Production deployments go through a quality gate chain:

**@sam** (security audit) → **@qualityguard** (quality check) → **@milo** (performance test) → **@riskguard** (compliance) → **@derek** (deploy) → **@vigil** (monitoring)

**Implementation:**

```python
def build_quality_gate_graph():
    graph = StateGraph(DeployState)
    
    graph.add_node("security", sam_node)
    graph.add_node("quality", qualityguard_node)
    graph.add_node("performance", milo_node)
    graph.add_node("compliance", riskguard_node)
    graph.add_node("deploy", derek_node)
    graph.add_node("monitor", vigil_node)
    graph.add_node("rollback", rollback_node)
    
    graph.add_edge(START, "security")
    graph.add_edge("security", "quality")
    graph.add_edge("quality", "performance")
    graph.add_edge("performance", "compliance")
    graph.add_edge("compliance", "deploy")
    graph.add_edge("deploy", "monitor")
    
    # If any gate fails, rollback
    def check_gate(state: DeployState) -> str:
        return "next" if state["gate_passed"] else "rollback"
    
    for gate in ["security", "quality", "performance", "compliance"]:
        graph.add_conditional_edges(gate, check_gate, {"next": gate, "rollback": "rollback"})
    
    graph.add_edge("rollback", END)
    graph.add_edge("monitor", END)
    
    return graph.compile()
```

---

## Error Handling & Retry Logic

**Circuit breaker pattern:**

```python
from langgraph.graph import StateGraph

class CircuitBreakerState(TypedDict):
    task: str
    attempts: int
    max_attempts: int
    result: str

def agent_with_retry(state: CircuitBreakerState) -> dict:
    try:
        result = execute_task(state["task"])
        return {"result": result, "attempts": 0}
    except Exception as e:
        attempts = state.get("attempts", 0) + 1
        if attempts >= state["max_attempts"]:
            return {"result": f"FAILED: {e}", "attempts": attempts}
        return {"attempts": attempts}

def route_retry(state: CircuitBreakerState) -> str:
    if state.get("result"):
        return "end"
    if state.get("attempts", 0) >= state["max_attempts"]:
        return "escalate"
    return "retry"

graph = StateGraph(CircuitBreakerState)
graph.add_node("execute", agent_with_retry)
graph.add_node("escalate", escalate_to_marcus)
graph.add_edge(START, "execute")
graph.add_conditional_edges("execute", route_retry, {
    "end": END,
    "retry": "execute",
    "escalate": "escalate"
})
graph.add_edge("escalate", END)
```

---

## State Schema Design

**Base state (all workflows inherit):**

```python
# state/base.py
from typing_extensions import TypedDict

class BaseState(TypedDict):
    workflow_id: str
    timestamp: str
    agent: str
    error: str | None
```

**Domain-specific state:**

```python
# state/order_state.py
from state.base import BaseState

class OrderState(BaseState):
    order_id: str
    items: list[dict]
    fitment_data: dict
    quality_passed: bool
    crm_synced: bool
```

---

## Deployment Architecture

**Current (Phase 1):**

```
GCP VM (e2-medium, 4GB RAM)
  ├── LangGraph runtime (Python)
  ├── Supabase client (state persistence)
  ├── MCP servers (tools)
  ├── n8n (email automation)
  └── Twenty CRM (client data)
```

**Future (Phase 6):**

```
GCP VM (e2-standard-2, 8GB RAM)
  ├── LangGraph runtime
  ├── OpenClaw (mobile interface)
  ├── Supabase client
  ├── MCP servers
  ├── n8n
  ├── Twenty CRM
  └── Monitoring (custom dashboards)
```

---

## Observability

**Logging:**

```python
import structlog

log = structlog.get_logger()

def agent_node(state):
    log.info("agent_execution_started", agent="hugo", workflow_id=state["workflow_id"])
    result = process(state)
    log.info("agent_execution_completed", agent="hugo", result=result)
    return result
```

**Monitoring dashboard (@dashboard builds):**

- Workflow success/failure rate
- Average execution time per agent
- State checkpoint frequency
- Error rate by agent
- API cost tracking (Claude, GPT, DeepSeek)

---

## Migration Path (5.0 → 6.0)

See [MIGRATION.md](MIGRATION.md) for the full plan.

**Summary:**

1. Phase 1 (Week 1-2): Foundation — LangGraph + first 5 agents
2. Phase 2 (Week 3-8): Agent migration — 70 agents as nodes
3. Phase 3 (Week 9-10): Orchestration — @marcus as supervisor
4. Phase 4 (Week 11-12): OpenClaw — mobile interface
5. Phase 5 (Week 13-16): Client pilots — AI receptionist
6. Phase 6 (Week 17-20): Hardening — production readiness

---

## Performance Targets

| Metric | Target | Current (5.0) |
|--------|--------|---------------|
| Workflow execution time | <10s for simple graphs | ~15-30s (manual scripts) |
| State checkpoint latency | <100ms | N/A (manual JSON writes) |
| Agent handoff latency | <50ms | ~500ms (chatroom.md polling) |
| Error recovery time | <5s (auto-retry) | Manual intervention required |
| Concurrent workflows | 10+ | 1-2 (blocking) |

---

## Security Model

**Principles:**

1. **Least privilege** — agents only access tools they need
2. **State isolation** — workflows can't access each other's state
3. **API key rotation** — weekly rotation for Claude, GPT, Supabase
4. **Audit logging** — all agent actions logged to Supabase
5. **Circuit breakers** — auto-kill runaway workflows

**RLS policies (Supabase):**

```sql
-- graph_state table: only workflow owner can read/write
CREATE POLICY "workflow_isolation" ON graph_state
  USING (workflow_id = current_setting('app.workflow_id')::text);

-- agents table: all agents can read, only @neo can write
CREATE POLICY "agent_read_all" ON agents FOR SELECT USING (true);
CREATE POLICY "agent_write_neo" ON agents FOR UPDATE 
  USING (current_setting('app.agent')::text = 'neo');
```

---

## Links

- **LangGraph docs:** https://github.com/langchain-ai/langgraph
- **Migration plan:** [MIGRATION.md](MIGRATION.md)
- **Agent authoring:** [docs/AGENT_AUTHORING.md](docs/AGENT_AUTHORING.md)
- **Cost analysis:** [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md)

---

*Jai.OS 6.0 — Production-grade orchestration for 70+ AI agents.*
