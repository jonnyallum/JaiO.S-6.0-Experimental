# security_audit

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : security_audit
SKILL : Security Audit — fetch security files, risk-score, report

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str) — immutable after entry
  Outputs  : security_report (str), risk_level (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram HIGH/CRITICAL alert [non-fatal]

Thread Memory (ch

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.security_audit import security_audit_node, build_graph

# Direct node call
result = security_audit_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_SECURITY_AUDIT_NAME="Custom Name"
PERSONA_SECURITY_AUDIT_NICKNAME="Custom Nick"
```
