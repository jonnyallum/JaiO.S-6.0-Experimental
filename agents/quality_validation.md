# quality_validation

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : quality_validation
SKILL : Quality Validation — score an artifact against its original request, PASS/FAIL gate

Node Contract (@langraph doctrine):
  Inputs   : artifact (str), artifact_type (str), original_query (str) — immutable after entry
  Outputs  : quality_score (int), quality_passed (bool), quality_feedback (str), error (str|None)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert o

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.quality_validation import quality_validation_node, build_graph

# Direct node call
result = quality_validation_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_QUALITY_VALIDATION_NAME="Custom Name"
PERSONA_QUALITY_VALIDATION_NICKNAME="Custom Nick"
```
