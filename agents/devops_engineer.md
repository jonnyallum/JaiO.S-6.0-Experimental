# devops_engineer

> **JaiOS 6.0 Agent Node**

## Description
DevOps Engineer - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), infra_context (str), output_type (VALID_OUTPUT_TYPES), platform (VALID_PLATFORMS)
    Outputs: devops_plan (str), config_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_ty

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.devops_engineer import devops_engineer_node, build_graph

# Direct node call
result = devops_engineer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_DEVOPS_ENGINEER_NAME="Custom Name"
PERSONA_DEVOPS_ENGINEER_NICKNAME="Custom Nick"
```
