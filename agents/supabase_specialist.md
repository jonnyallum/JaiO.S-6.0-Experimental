# supabase_specialist

> **JaiOS 6.0 Agent Node**

## Description
Supabase Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), project_context (str), output_type (VALID_OUTPUT_TYPES), area (VALID_AREAS)
    Outputs: supabase_spec (str), sql_output (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.supabase_specialist import supabase_specialist_node, build_graph

# Direct node call
result = supabase_specialist_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SUPABASE_SPECIALIST_NAME="Custom Name"
PERSONA_SUPABASE_SPECIALIST_NICKNAME="Custom Nick"
```
