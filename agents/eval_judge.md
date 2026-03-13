# eval_judge

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 eval_judge — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : content (str), criteria (str), rubric (str — optional)
 Output keys : score (int 0-100), verdict (str), feedback (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 LLM-as-judge quality scoring agent

 Persona: identity injected at runtime via personas/config.py
━━━━━━━━━━━━━

## State Contract
- **State class**: `EvalJudgeState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.eval_judge import eval_judge_node, build_graph

# Direct node call
result = eval_judge_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_EVAL_JUDGE_NAME="Custom Name"
PERSONA_EVAL_JUDGE_NICKNAME="Custom Nick"
```
