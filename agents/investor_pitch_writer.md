# investor_pitch_writer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 investor_pitch_writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : company_name (str), one_liner (str),
               funding_stage (str), raise_amount (str),
               problem (str), solution (str),
               traction (str — optional metrics/milestones),
               market_size (str — optional TAM/SAM/SOM),
               output_type (str)
 

## State Contract
- **State class**: `InvestorPitchState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.investor_pitch_writer import investor_pitch_writer_node, build_graph

# Direct node call
result = investor_pitch_writer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_INVESTOR_PITCH_WRITER_NAME="Custom Name"
PERSONA_INVESTOR_PITCH_WRITER_NICKNAME="Custom Nick"
```
