# dependency_audit

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : dependency_audit
SKILL : Dependency Audit — collect package manifests, identify risks, produce upgrade plan

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), focus (str) — immutable after entry
  Outputs  : dependency_report (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.dependency_audit import dependency_audit_node, build_graph

# Direct node call
result = dependency_audit_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_DEPENDENCY_AUDIT_NAME="Custom Name"
PERSONA_DEPENDENCY_AUDIT_NICKNAME="Custom Nick"
```
