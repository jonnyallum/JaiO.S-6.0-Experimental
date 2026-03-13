# api_integration_agent

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 api_integration_agent — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : api_description (str), task (str), auth_type (str — optional)
 Output keys : integration_plan (str), sample_request (str), error_handling (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Generic API integration and calling agent

 Persona: identity injected at r

## State Contract
- **State class**: `ApiIntegrationAgentState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.api_integration_agent import api_integration_agent_node, build_graph

# Direct node call
result = api_integration_agent_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_API_INTEGRATION_AGENT_NAME="Custom Name"
PERSONA_API_INTEGRATION_AGENT_NICKNAME="Custom Nick"
```
