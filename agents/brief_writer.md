# brief_writer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : brief_writer
SKILL : Brief Writer — context + goal → structured client brief, proposal, or scope of work

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), brief_type (str), context (str), goal (str),
             budget_hint (str), timeline_hint (str) — immutable after entry
  Outputs  : brief (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegr

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.brief_writer import brief_writer_node, build_graph

# Direct node call
result = brief_writer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_BRIEF_WRITER_NAME="Custom Name"
PERSONA_BRIEF_WRITER_NICKNAME="Custom Nick"
```
