# business_intelligence

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : business_intelligence
SKILL : Business Intelligence — KPI data + context → executive report, trend analysis, decision recommendations

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), kpi_data (str), period (str),
             goals (str), context (str) — immutable after entry
  Outputs  : bi_report (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.business_intelligence import business_intelligence_node, build_graph

# Direct node call
result = business_intelligence_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_BUSINESS_INTELLIGENCE_NAME="Custom Name"
PERSONA_BUSINESS_INTELLIGENCE_NICKNAME="Custom Nick"
```
