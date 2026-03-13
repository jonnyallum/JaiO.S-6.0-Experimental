# launch_orchestrator

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 launch_orchestrator — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), launch_type (str), channels (str),
               launch_date (str — ISO YYYY-MM-DD), audience (str),
               current_date (str — ISO, optional — defaults to today)
 Output keys : launch_plan (str), timeline (dict)
 Side effects: Supabase PRE/POST checkpoints, CallM

## State Contract
- **State class**: `LaunchState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.launch_orchestrator import launch_orchestrator_node, build_graph

# Direct node call
result = launch_orchestrator_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_LAUNCH_ORCHESTRATOR_NAME="Custom Name"
PERSONA_LAUNCH_ORCHESTRATOR_NICKNAME="Custom Nick"
```
