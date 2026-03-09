# Agent Nodes

Each agent is a LangGraph node — a Python function that receives state and returns updates.

## Agent Authoring Rules

1. **Pure functions** — no side effects in state logic
2. **Return dict** — keys must match state TypedDict fields
3. **Error handling** — raise exceptions, LangGraph catches them
4. **Tool calls** — use MCP servers for external operations
5. **Logging** — use structured logging

## Template

```python
from typing_extensions import TypedDict
import structlog

log = structlog.get_logger()

class AgentState(TypedDict):
    input: str
    output: str

def agent_node(state: AgentState) -> dict:
    """
    @agent — Role description
    What this agent does.
    """
    log.info("agent_started", agent="agent_name")
    
    try:
        result = process(state["input"])
        log.info("agent_completed", result=result)
        return {"output": result}
    except Exception as e:
        log.error("agent_failed", error=str(e))
        raise
```

## First 5 Agents (Phase 1)

- **hugo.py** — GitHub intelligence
- **parser.py** — Data extraction
- **qualityguard.py** — Quality gates
- **sam.py** — Security audits
- **sebastian.py** — Full-stack architect