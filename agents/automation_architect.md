# automation_architect

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : automation_architect
SKILL : Automation Architecture — workflow description → n8n node spec, trigger config, implementation plan

Node Contract (@langraph doctrine):
  Inputs   : workflow_description (str), tools_available (str),
             trigger_type (str), complexity (str) — immutable after entry
  Outputs  : automation_spec (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.automation_architect import automation_architect_node, build_graph

# Direct node call
result = automation_architect_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_AUTOMATION_ARCHITECT_NAME="Custom Name"
PERSONA_AUTOMATION_ARCHITECT_NICKNAME="Custom Nick"
```
