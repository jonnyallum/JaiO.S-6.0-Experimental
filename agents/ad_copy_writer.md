# ad_copy_writer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ad_copy_writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product (str), audience (str), platform (str),
               objective (str), usp (str — unique selling point),
               num_variants (int — default 3)
 Output keys : ad_variants (list[dict]), variant_count (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Pol

## State Contract
- **State class**: `AdCopyState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.ad_copy_writer import ad_copy_writer_node, build_graph

# Direct node call
result = ad_copy_writer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_AD_COPY_WRITER_NAME="Custom Name"
PERSONA_AD_COPY_WRITER_NICKNAME="Custom Nick"
```
