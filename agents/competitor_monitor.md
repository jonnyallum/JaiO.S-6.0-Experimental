# competitor_monitor

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : competitor_monitor
SKILL : Competitor Monitor — scrape a public competitor URL, extract positioning signals,
        synthesise strategic intel report with gap analysis and opportunity map

Node Contract (@langraph doctrine):
  Inputs   : competitor_url (str), our_context (str), focus (str) — immutable after entry
  Outputs  : intel_report (str), error (str|None), agent (str)
  Tools    : requests [read-only scrape], Anthropic [read-

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.competitor_monitor import competitor_monitor_node, build_graph

# Direct node call
result = competitor_monitor_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_COMPETITOR_MONITOR_NAME="Custom Name"
PERSONA_COMPETITOR_MONITOR_NICKNAME="Custom Nick"
```
