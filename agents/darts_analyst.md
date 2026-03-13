# darts_analyst

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 darts_analyst — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : match (str), tournament (str — optional), market (str — match_winner/legs/180s)
 Output keys : analysis (str), statistical_edge (str), picks (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Darts statistics, averages, and checkout route analysis

 Persona: identity inje

## State Contract
- **State class**: `DartsAnalystState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.darts_analyst import darts_analyst_node, build_graph

# Direct node call
result = darts_analyst_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_DARTS_ANALYST_NAME="Custom Name"
PERSONA_DARTS_ANALYST_NICKNAME="Custom Nick"
```
