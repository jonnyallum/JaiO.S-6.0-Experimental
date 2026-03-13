# football_tactical

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 football_tactical — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : match (str), league (str — optional), bet_type (str — match_result/goals/cards/corners)
 Output keys : tactical_report (str), value_bets (str), confidence (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Football tactical intelligence and value betting analysis

 Pe

## State Contract
- **State class**: `FootballTacticalState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.football_tactical import football_tactical_node, build_graph

# Direct node call
result = football_tactical_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_FOOTBALL_TACTICAL_NAME="Custom Name"
PERSONA_FOOTBALL_TACTICAL_NICKNAME="Custom Nick"
```
