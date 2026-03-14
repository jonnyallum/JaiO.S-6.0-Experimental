"""
Jai.OS 6.0 — Memory Spine Module

Provides vector-backed persistent memory for the 93-agent Hive Mind.
Architecture: PostgreSQL 16 + pgvector (primary) with future Qdrant hot-path.

Quick Start:
    from memory.agent_mixin import AgentMemory

    mem = AgentMemory("deep_researcher")
    mem.remember("pgvector outperforms Pinecone for <100M vectors", memory_type="semantic")
    results = mem.recall("vector database performance", k=3)
    mem.share("Key finding: pgvector is optimal for our scale", importance=0.8)
    combined = mem.recall_with_shared("database recommendations", k=3)

API Endpoints (via api/main.py):
    GET  /memory/health         — System-wide stats + per-agent breakdown
    GET  /memory/agent/{id}     — Single agent memory detail
    GET  /memory/search?q=...   — Semantic search across all memories
    POST /memory/decay          — Trigger decay cycle
    POST /memory/share          — Create shared cross-agent memory
"""

# Embedding configuration
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

# Memory types
MEMORY_TYPES = [
    "episodic",      # event-based, temporal
    "semantic",      # factual knowledge
    "procedural",    # how-to, workflow steps
    "reflective",    # meta-cognitive / self-assessment
    "shared",        # cross-agent consensus memory
]

# Memory statuses
MEMORY_STATUSES = [
    "pending_embed",  # awaiting embedding generation
    "active",         # searchable and live
    "superseded",     # replaced by newer memory
    "archived",       # cold storage (decayed below threshold)
    "poisoned",       # flagged as corrupted/unreliable (ancient + never accessed)
]

# Link types for associative memory graph
LINK_TYPES = [
    "causal",         # A caused B
    "temporal",       # A happened before B
    "contradicts",    # A conflicts with B
    "supports",       # A reinforces B
    "refines",        # A is a more specific version of B
    "supersedes",     # A replaces B
    "associates",     # loosely related
]

# Default search parameters
DEFAULT_SEARCH_K = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.7
DEFAULT_DECAY_DAYS = 14

# Public API — convenience imports
__all__ = [
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "MEMORY_TYPES",
    "MEMORY_STATUSES",
    "LINK_TYPES",
]
