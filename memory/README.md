# Memory Spine — Jai.OS 6.0

> Persistent vector-backed memory for the 93-agent Hive Mind.

## Architecture

```text
Agent Code → AgentMemory API → MemoryStore → PostgreSQL 16 + pgvector
                                    ↕
                              OpenAI Embeddings
                            (text-embedding-3-small)
```

## Quick Start

```python
from memory.agent_mixin import AgentMemory

mem = AgentMemory("hugo")

# Store a memory
mid = mem.remember(
    "PostgreSQL outperforms Pinecone for <100M vectors",
    memory_type="semantic",
    importance=0.8,
    tags=["database", "performance"],
)

# Search by meaning
results = mem.recall("best database for vector search", k=5)

# Get formatted context for prompts
context = mem.reflect("vector database architecture")

# Keyword search (faster, for exact terms)
exact = mem.recall_keyword("pgvector HNSW")

# Link related memories
mem.link(memory_id_1, memory_id_2, link_type="supports")

# Check stats
print(mem.stats())
```

## Memory Types

| Type | Purpose |
| ---- | ------- |
| `episodic` | Event-based, temporal (default) |
| `semantic` | Factual knowledge |
| `procedural` | How-to, workflow steps |
| `reflective` | Meta-cognitive, self-assessment |
| `shared` | Cross-agent consensus memory |

## File Structure

```text
memory/
├── __init__.py        # Constants and configuration
├── connection.py      # Database connection management
├── models.py          # Pydantic data models
├── embedding.py       # OpenAI embedding generation
├── store.py           # Core MemoryStore (CRUD + search)
├── agent_mixin.py     # Agent-facing API (remember/recall/reflect)
├── apply_migrations.py # Migration runner
├── smoke_test.py      # End-to-end validation
└── README.md          # This file
```

## Migrations

```bash
# Apply all migrations
python -m memory.apply_migrations

# Apply specific migration
python -m memory.apply_migrations 003

# Dry run (show what would run)
python -m memory.apply_migrations --dry
```

## Database Schema

- **`memories`** — Core memory records with pgvector embeddings
- **`memory_chunks`** — RAG-style sub-segments for granular retrieval
- **`memory_links`** — Associative graph between memories
- **`memory_access_log`** — Read-path tracking (for decay analytics)
- **`memory_audit_log`** — Immutable append-only audit trail
- **`coordinator_groups`** — Memory coordinator cluster definitions

## Search Types

1. **Vector Similarity** (`recall`) — Semantic search using cosine distance
2. **Keyword** (`recall_keyword`) — Full-text search using PostgreSQL tsvector
3. **Hybrid** — Combine both for maximum precision (future Phase 3)

## Memory Decay

Memories that are not accessed for 30+ days have their `decay_factor` reduced by 10%. Memories that decay below 0.1 are automatically archived.

```python
from memory.store import MemoryStore
store = MemoryStore()
affected = store.decay_stale_memories(agent_id="hugo", older_than_days=30)
```
