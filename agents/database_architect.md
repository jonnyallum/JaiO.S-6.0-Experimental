# database_architect

> **JaiOS 6.0 Agent Node**

## Description
Database Architect - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), db_context (str), output_type (VALID_OUTPUT_TYPES), db_engine (VALID_DB_ENGINES)
    Outputs: db_design (str), schema_summary (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_t

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.database_architect import database_architect_node, build_graph

# Direct node call
result = database_architect_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_DATABASE_ARCHITECT_NAME="Custom Name"
PERSONA_DATABASE_ARCHITECT_NICKNAME="Custom Nick"
```
