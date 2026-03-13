# ecommerce_strategist

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ecommerce_strategist — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), niche (str), output_type (str),
               cost_price (float — optional, GBP),
               sell_price (float — optional, GBP),
               platform_fee_pct (float — optional, default 0.13),
               shipping_cost (float — optional, GBP),
               con

## State Contract
- **State class**: `EcommerceState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.ecommerce_strategist import ecommerce_strategist_node, build_graph

# Direct node call
result = ecommerce_strategist_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_ECOMMERCE_STRATEGIST_NAME="Custom Name"
PERSONA_ECOMMERCE_STRATEGIST_NICKNAME="Custom Nick"
```
