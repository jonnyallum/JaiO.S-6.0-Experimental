# chatbot_designer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 chatbot_designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : bot_name (str), bot_purpose (str), platform (str),
               audience (str), tone (str), output_type (str),
               key_intents (str — comma-separated user goals),
               escalation_path (str — optional)
 Output keys : chatbot_design (str), intent_count (int)
 Side effects: S

## State Contract
- **State class**: `ChatbotState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.chatbot_designer import chatbot_designer_node, build_graph

# Direct node call
result = chatbot_designer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_CHATBOT_DESIGNER_NAME="Custom Name"
PERSONA_CHATBOT_DESIGNER_NICKNAME="Custom Nick"
```
