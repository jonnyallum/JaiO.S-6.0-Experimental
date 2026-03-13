# sales_conversion

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : sales_conversion
SKILL : Sales Conversion — prospect profile + objections → close strategy, scripts, next actions

Node Contract (@langraph doctrine):
  Inputs   : prospect_name (str), company (str), deal_stage (str),
             context (str), objections (str) — immutable after entry
  Outputs  : close_strategy (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Teleg

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.sales_conversion import sales_conversion_node, build_graph

# Direct node call
result = sales_conversion_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SALES_CONVERSION_NAME="Custom Name"
PERSONA_SALES_CONVERSION_NICKNAME="Custom Nick"
```
