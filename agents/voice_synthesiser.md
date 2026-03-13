# voice_synthesiser

> **JaiOS 6.0 Agent Node**

## Description
ElevenLabs Voice Synthesis Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : script_brief (str), voice_use (VALID_VOICE_USES), tone_style (VALID_TONE_STYLES), duration_target_seconds (int)
    Outputs: production_script (str), voice_direction (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty brief, invalid use) raise immediately.

Failure Discrimination:
    P

## State Contract
- **State class**: `Unknown`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.voice_synthesiser import voice_synthesiser_node, build_graph

# Direct node call
result = voice_synthesiser_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_VOICE_SYNTHESISER_NAME="Custom Name"
PERSONA_VOICE_SYNTHESISER_NICKNAME="Custom Nick"
```
