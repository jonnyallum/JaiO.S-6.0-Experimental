# legal_advisor

> **JaiOS 6.0 Agent Node**

## Description
Legal Advisor - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), legal_context (str), output_type (VALID_OUTPUT_TYPES), jurisdiction (VALID_JURISDICTIONS)
    Outputs: legal_advice (str), risk_level (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown outpu

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.legal_advisor import legal_advisor_node, build_graph

# Direct node call
result = legal_advisor_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_LEGAL_ADVISOR_NAME="Custom Name"
PERSONA_LEGAL_ADVISOR_NICKNAME="Custom Nick"
```
