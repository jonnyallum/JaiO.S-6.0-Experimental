# process_auditor

> **JaiOS 6.0 Agent Node**

## Description
Process Friction Detector - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : process_description (str), process_type (VALID_PROCESS_TYPES), output_type (VALID_OUTPUT_TYPES)
    Outputs: audit_report (str), friction_count (int), bottleneck_score (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty description, invalid type) raise immediately.

Failure Discrimination:
    PERM

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.process_auditor import process_auditor_node, build_graph

# Direct node call
result = process_auditor_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_PROCESS_AUDITOR_NAME="Custom Name"
PERSONA_PROCESS_AUDITOR_NICKNAME="Custom Nick"
```
