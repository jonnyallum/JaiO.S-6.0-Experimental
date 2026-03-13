# motogp_analyst

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 motogp_analyst — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : race (str), class (str — motogp/moto2/moto3), market (str)
 Output keys : race_preview (str), betting_angles (str), risk_factors (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 MotoGP telemetry, race craft, and betting analysis

 Persona: identity injected at runtime 

## State Contract
- **State class**: `MotogpAnalystState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.motogp_analyst import motogp_analyst_node, build_graph

# Direct node call
result = motogp_analyst_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_MOTOGP_ANALYST_NAME="Custom Name"
PERSONA_MOTOGP_ANALYST_NICKNAME="Custom Nick"
```
