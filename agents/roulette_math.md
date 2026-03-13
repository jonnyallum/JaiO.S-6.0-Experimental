# roulette_math

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 roulette_math — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : game_type (str — european/american/french), strategy (str), bankroll (str — optional)
 Output keys : mathematical_analysis (str), expected_value (str), risk_of_ruin (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Roulette probability, casino mathematics, and edge analy

## State Contract
- **State class**: `RouletteMathState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.roulette_math import roulette_math_node, build_graph

# Direct node call
result = roulette_math_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_ROULETTE_MATH_NAME="Custom Name"
PERSONA_ROULETTE_MATH_NICKNAME="Custom Nick"
```
