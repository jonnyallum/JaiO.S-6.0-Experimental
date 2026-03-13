# email_architect

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : email_architect
SKILL : Email Architect — design and write complete email sequences with subject lines,
        body copy, and CTAs; hard-capped at EMAIL_LIMIT emails per sequence

Node Contract (@langraph doctrine):
  Inputs   : sequence_goal (str), audience (str), product (str), num_emails (int),
             tone (str), from_name (str) — immutable after entry
  Outputs  : email_sequence (str), email_count (int), error (str|None), 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.email_architect import email_architect_node, build_graph

# Direct node call
result = email_architect_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_EMAIL_ARCHITECT_NAME="Custom Name"
PERSONA_EMAIL_ARCHITECT_NICKNAME="Custom Nick"
```
