# social_post_generator

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : social_post_generator
SKILL : Social Post Generator — brief → FB/IG copy, optional publish via Meta Graph API

Node Contract (@langraph doctrine):
  Inputs   : brief (str), platform (str), tone (str), hashtags (str),
             publish (bool), image_url (str|None) — immutable after entry
  Outputs  : post_copy (dict), published (bool), post_ids (dict), error (str|None)
  Tools    : Anthropic [read-only for generation], MetaSocialTo

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.social_post_generator import social_post_generator_node, build_graph

# Direct node call
result = social_post_generator_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SOCIAL_POST_GENERATOR_NAME="Custom Name"
PERSONA_SOCIAL_POST_GENERATOR_NICKNAME="Custom Nick"
```
