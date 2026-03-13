# truth_verifier

> **JaiOS 6.0 Agent Node**

## Description
Truth Verifier - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : artifact (str), artifact_type (VALID_ARTIFACT_TYPES), check_level (VALID_CHECK_LEVELS)
    Outputs: verification_report (str), gates_passed (int), gates_failed (int), confidence (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty artifact, invalid type) raise immediately.

Failure Discrimination:
    PERMANEN

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.truth_verifier import truth_verifier_node, build_graph

# Direct node call
result = truth_verifier_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_TRUTH_VERIFIER_NAME="Custom Name"
PERSONA_TRUTH_VERIFIER_NICKNAME="Custom Nick"
```
