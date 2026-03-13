# funnel_architect

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : funnel_architect
SKILL : Funnel Architect — design a complete conversion funnel with stage-specific copy
        angles, offer structure, objection matrix, upsell map, and CRO recommendations

Node Contract (@langraph doctrine):
  Inputs   : product (str), audience (str), funnel_stage (str), traffic_source (str),
             avg_order_value (str), current_conversion (str) — immutable after entry
  Outputs  : funnel_spec (str), error

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.funnel_architect import funnel_architect_node, build_graph

# Direct node call
result = funnel_architect_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_FUNNEL_ARCHITECT_NAME="Custom Name"
PERSONA_FUNNEL_ARCHITECT_NICKNAME="Custom Nick"
```
