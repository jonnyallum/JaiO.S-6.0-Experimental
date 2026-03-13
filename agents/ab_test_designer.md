# ab_test_designer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ab_test_designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : page_or_element (str — what's being tested),
               hypothesis (str — "We believe X will Y because Z"),
               baseline_cvr (float — current conversion rate 0.0–1.0),
               mde (float — minimum detectable effect, e.g. 0.2 = 20% rel. lift),
               daily_visitors (

## State Contract
- **State class**: `ABTestState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.ab_test_designer import ab_test_designer_node, build_graph

# Direct node call
result = ab_test_designer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_AB_TEST_DESIGNER_NAME="Custom Name"
PERSONA_AB_TEST_DESIGNER_NICKNAME="Custom Nick"
```
