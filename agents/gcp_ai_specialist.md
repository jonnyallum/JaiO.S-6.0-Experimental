# gcp_ai_specialist

> **JaiOS 6.0 Agent Node**

## Description
GCP AI Platform Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), gcp_context (str), output_type (VALID_OUTPUT_TYPES), gcp_service (VALID_GCP_SERVICES)
    Outputs: gcp_spec (str), terraform_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, un

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.gcp_ai_specialist import gcp_ai_specialist_node, build_graph

# Direct node call
result = gcp_ai_specialist_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_GCP_AI_SPECIALIST_NAME="Custom Name"
PERSONA_GCP_AI_SPECIALIST_NICKNAME="Custom Nick"
```
