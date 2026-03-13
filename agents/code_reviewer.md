# code_reviewer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : code_reviewer
SKILL : Code Review — fetch specific files, produce file-level review with line-referenced findings

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), file_paths (list[str]), focus (str) — immutable after entry
  Outputs  : code_review (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram al

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.code_reviewer import code_reviewer_node, build_graph

# Direct node call
result = code_reviewer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_CODE_REVIEWER_NAME="Custom Name"
PERSONA_CODE_REVIEWER_NICKNAME="Custom Nick"
```
