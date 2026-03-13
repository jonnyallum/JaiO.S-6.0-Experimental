# content_auditor

> **JaiOS 6.0 Agent Node**

## Description
Content Depth Auditor - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : content (str), content_type (VALID_CONTENT_TYPES), audit_focus (VALID_AUDIT_FOCUSES)
    Outputs: audit_report (str), depth_score (int), fluff_count (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty content, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty content, unk

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.content_auditor import content_auditor_node, build_graph

# Direct node call
result = content_auditor_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_CONTENT_AUDITOR_NAME="Custom Name"
PERSONA_CONTENT_AUDITOR_NICKNAME="Custom Nick"
```
