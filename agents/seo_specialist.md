# seo_specialist

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : seo_specialist
SKILL : SEO Specialist — extract on-page signals, produce structured audit with schema
        recommendations, keyword gap analysis, and a prioritised 30-day action plan

Node Contract (@langraph doctrine):
  Inputs   : url (str), page_content (str), target_keywords (str),
             business_context (str), focus (str) — immutable after entry
  Outputs  : seo_report (str), error (str|None), agent (str)
  Tools    : 

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.seo_specialist import seo_specialist_node, build_graph

# Direct node call
result = seo_specialist_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SEO_SPECIALIST_NAME="Custom Name"
PERSONA_SEO_SPECIALIST_NICKNAME="Custom Nick"
```
