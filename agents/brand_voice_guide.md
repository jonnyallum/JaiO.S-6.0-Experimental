# brand_voice_guide

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 brand_voice_guide — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : brand_name (str), industry (str),
               audience (str), tone_keywords (str — 3–6 adjectives),
               brand_values (str — optional),
               sample_content (str — optional existing brand copy),
               output_type (str)
 Output keys : voice_guide (str), voice_spect

## State Contract
- **State class**: `BrandVoiceState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.brand_voice_guide import brand_voice_guide_node, build_graph

# Direct node call
result = brand_voice_guide_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_BRAND_VOICE_GUIDE_NAME="Custom Name"
PERSONA_BRAND_VOICE_GUIDE_NICKNAME="Custom Nick"
```
