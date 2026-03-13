# supabase_intelligence

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : supabase_intelligence
SKILL : Shared Brain Intelligence — query the live agent database and synthesise a status report

Node Contract (@langraph doctrine):
  Inputs   : query (str), focus (str) — immutable after entry
  Outputs  : intelligence (str), error (str|None), agent (str)
  Tools    : Supabase [read-only — agents, learnings, chatroom, graph_state], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal, graph_state 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.supabase_intelligence import supabase_intelligence_node, build_graph

# Direct node call
result = supabase_intelligence_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SUPABASE_INTELLIGENCE_NAME="Custom Name"
PERSONA_SUPABASE_INTELLIGENCE_NICKNAME="Custom Nick"
```
