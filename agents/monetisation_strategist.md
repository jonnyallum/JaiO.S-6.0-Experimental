# monetisation_strategist

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : monetisation_strategist
SKILL : Monetisation Strategy — business context + goals → revenue blueprint, pricing, funnel design

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), business_context (str), current_revenue (str),
             goals (str), constraints (str) — immutable after entry
  Outputs  : strategy (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [n

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.monetisation_strategist import monetisation_strategist_node, build_graph

# Direct node call
result = monetisation_strategist_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_MONETISATION_STRATEGIST_NAME="Custom Name"
PERSONA_MONETISATION_STRATEGIST_NICKNAME="Custom Nick"
```
