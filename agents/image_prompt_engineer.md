# image_prompt_engineer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 image_prompt_engineer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : concept (str), style (str — optional), model (str — dall-e/midjourney/stable-diffusion)
 Output keys : prompt (str), negative_prompt (str), parameters (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Image generation prompt crafting agent

 Persona: identity inj

## State Contract
- **State class**: `ImagePromptEngineerState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.image_prompt_engineer import image_prompt_engineer_node, build_graph

# Direct node call
result = image_prompt_engineer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_IMAGE_PROMPT_ENGINEER_NAME="Custom Name"
PERSONA_IMAGE_PROMPT_ENGINEER_NICKNAME="Custom Nick"
```
