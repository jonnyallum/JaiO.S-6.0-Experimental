# creative_director

> **JaiOS 6.0 Agent Node**

## Description
Creative Director - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), brand_context (str), output_type (VALID_OUTPUT_TYPES), medium (VALID_MEDIUMS)
    Outputs: creative_brief (str), direction_notes (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.creative_director import creative_director_node, build_graph

# Direct node call
result = creative_director_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_CREATIVE_DIRECTOR_NAME="Custom Name"
PERSONA_CREATIVE_DIRECTOR_NICKNAME="Custom Nick"
```
