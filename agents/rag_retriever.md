# rag_retriever

> **JaiOS 6.0 Agent Node**

## Description
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 rag_retriever — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : query (str), knowledge_base (str — identifier), top_k (int — optional)
 Output keys : retrieved_context (str), answer (str), sources (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Vector search and semantic retrieval agent

 Persona: identity injected at runtime via p

## State Contract
- **State class**: `RagRetrieverState`
- **Input keys**: `task` (str), `context` (str), `thread_id` (str)
- **Output keys**: `output` (str), `error` (str)

## Usage
```python
from agents.rag_retriever import rag_retriever_node, build_graph

# Direct node call
result = rag_retriever_node({"task": "your task here", "context": "", "thread_id": "t1"})

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
PERSONA_RAG_RETRIEVER_NAME="Custom Name"
PERSONA_RAG_RETRIEVER_NICKNAME="Custom Nick"
```
