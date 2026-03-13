# video_brief_writer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : video_brief_writer
SKILL : Video Brief Writer — produce a director-ready short-form video brief with hook,
        scene-by-scene script, b-roll shot list, caption, and thumbnail concept

Node Contract (@langraph doctrine):
  Inputs   : topic (str), platform (str), duration_seconds (int), hook_style (str),
             cta (str), brand_context (str) — immutable after entry
  Outputs  : video_brief (str), error (str|None), agent (str)

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.video_brief_writer import video_brief_writer_node, build_graph

# Direct node call
result = video_brief_writer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_VIDEO_BRIEF_WRITER_NAME="Custom Name"
PERSONA_VIDEO_BRIEF_WRITER_NICKNAME="Custom Nick"
```
