# formula1_analyst

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 formula1_analyst — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : race (str), session (str — qualifying/race/sprint), market (str)
 Output keys : strategy_report (str), predictions (str), value_angles (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Formula 1 race strategy, qualifying, and betting analysis

 Persona: identity injec

## State Contract
- **State class**: `Formula1AnalystState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.formula1_analyst import formula1_analyst_node, build_graph

# Direct node call
result = formula1_analyst_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_FORMULA1_ANALYST_NAME="Custom Name"
PERSONA_FORMULA1_ANALYST_NICKNAME="Custom Nick"
```
