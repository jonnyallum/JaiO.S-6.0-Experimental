# onboarding_agent

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 onboarding_agent — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : client_info (str), project_type (str), budget_range (str — optional)
 Output keys : onboarding_plan (str), questionnaire (str), timeline (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Client onboarding automation agent

 Persona: identity injected at runtime via pe

## State Contract
- **State class**: `OnboardingAgentState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.onboarding_agent import onboarding_agent_node, build_graph

# Direct node call
result = onboarding_agent_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_ONBOARDING_AGENT_NAME="Custom Name"
PERSONA_ONBOARDING_AGENT_NICKNAME="Custom Nick"
```
