# fact_checker

> **JaiOS 6.0 Agent Node**

## Description
Fact Checker - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : claim (str), supporting_context (str), output_type (VALID_OUTPUT_TYPES), domain (VALID_DOMAINS)
    Outputs: fact_check_report (str), verdict (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty claim, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty claim, unknown output_t

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.fact_checker import fact_checker_node, build_graph

# Direct node call
result = fact_checker_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_FACT_CHECKER_NAME="Custom Name"
PERSONA_FACT_CHECKER_NICKNAME="Custom Nick"
```
