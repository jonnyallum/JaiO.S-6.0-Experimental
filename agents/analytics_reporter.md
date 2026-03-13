# analytics_reporter

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 analytics_reporter — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : raw_data (dict | str), focus (str), period (str),
               goal (str — optional business objective)
 Output keys : analytics_report (str), key_metrics (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: P

## State Contract
- **State class**: `AnalyticsState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.analytics_reporter import analytics_reporter_node, build_graph

# Direct node call
result = analytics_reporter_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_ANALYTICS_REPORTER_NAME="Custom Name"
PERSONA_ANALYTICS_REPORTER_NICKNAME="Custom Nick"
```
