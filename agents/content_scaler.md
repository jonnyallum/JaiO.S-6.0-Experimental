# content_scaler

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : content_scaler
SKILL : Content Scaling — topic + brand voice → multiple A/B copy variants for testing

Node Contract (@langraph doctrine):
  Inputs   : topic (str), brand_voice (str), platform (str),
             variant_count (int), cta (str) — immutable after entry
  Outputs  : variants (list[str]), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.content_scaler import content_scaler_node, build_graph

# Direct node call
result = content_scaler_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_CONTENT_SCALER_NAME="Custom Name"
PERSONA_CONTENT_SCALER_NICKNAME="Custom Nick"
```
