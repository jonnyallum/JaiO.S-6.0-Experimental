# venture_ideator

> **JaiOS 6.0 Agent Node**

## Description
Creative Venture Architect — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : idea_context (str), idea_type (VALID_IDEA_TYPES), market_size (VALID_MARKET_SIZES), budget_hint (str)
    Outputs: venture_blueprint (str), viability_score (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty context, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.venture_ideator import venture_ideator_node, build_graph

# Direct node call
result = venture_ideator_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_VENTURE_IDEATOR_NAME="Custom Name"
PERSONA_VENTURE_IDEATOR_NICKNAME="Custom Nick"
```
