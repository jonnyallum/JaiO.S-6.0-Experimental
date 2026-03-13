# workflow_planner

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 workflow_planner — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : task (str), constraints (str — optional), available_agents (str — optional)
 Output keys : plan (str), steps (str), agent_assignments (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Task decomposition and workflow planning agent

 Persona: identity injected at runti

## State Contract
- **State class**: `WorkflowPlannerState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.workflow_planner import workflow_planner_node, build_graph

# Direct node call
result = workflow_planner_node({"task": "your task here", "context": "", "thread_id": "t1"})

# Via compiled graph
graph = build_graph()
result = graph.invoke({"task": "your task here", "context": "", "thread_id": "t1"})
```

## Error Handling
- `PERMANENT: ...` — input validation failure, do not retry
- `TRANSIENT: ...` — API error, safe to retry (auto-retried 3x)
- `UNEXPECTED: ...` — unknown error, investigate

## Persona
Identity injected at runtime via `personas/config.py`. Override with env vars:
```
PERSONA_WORKFLOW_PLANNER_NAME="Custom Name"
PERSONA_WORKFLOW_PLANNER_NICKNAME="Custom Nick"
```
