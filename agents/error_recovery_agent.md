# error_recovery_agent

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 error_recovery_agent — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : error_info (str), context (str), system (str — optional)
 Output keys : diagnosis (str), classification (str), recovery_plan (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Automated error diagnosis and recovery agent

 Persona: identity injected at runtime via 

## State Contract
- **State class**: `ErrorRecoveryAgentState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.error_recovery_agent import error_recovery_agent_node, build_graph

# Direct node call
result = error_recovery_agent_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_ERROR_RECOVERY_AGENT_NAME="Custom Name"
PERSONA_ERROR_RECOVERY_AGENT_NICKNAME="Custom Nick"
```
