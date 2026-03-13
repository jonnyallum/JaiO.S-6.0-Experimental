# course_designer

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 course_designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : course_title (str), target_student (str),
               transformation (str — what student can do after),
               delivery_format (str), num_modules (int),
               expertise_level (str)
 Output keys : curriculum (str), module_count (int)
 Side effects: Supabase PRE/POST checkpoints

## State Contract
- **State class**: `CourseState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.course_designer import course_designer_node, build_graph

# Direct node call
result = course_designer_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_COURSE_DESIGNER_NAME="Custom Name"
PERSONA_COURSE_DESIGNER_NICKNAME="Custom Nick"
```
