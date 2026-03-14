"""
Agent Memory Mixin — Simple API for agents to interact with the Memory Spine.

Provides three core operations:
    remember(content) — store a new memory
    recall(query)     — semantic search for relevant memories
    reflect(topic)    — retrieve and summarize related memories

Usage in an agent:
    from memory.agent_mixin import AgentMemory

    mem = AgentMemory("deep_researcher")
    mem.remember("Discovered pgvector outperforms Pinecone for <100M vectors", memory_type="semantic")
    results = mem.recall("vector database performance", k=3)
"""
import logging
from uuid import UUID

from memory.store import MemoryStore
from memory.models import Memory, SearchResult

log = logging.getLogger(__name__)

# Singleton store instance
_store: MemoryStore | None = None


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


class AgentMemory:
    """
    Simple memory interface for a specific agent.
    Each agent creates one instance with their handle.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.store = _get_store()

    # ── REMEMBER ─────────────────────────────────────────────────

    def remember(
        self,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        source_uri: str | None = None,
    ) -> UUID:
        """
        Store a new memory.

        Args:
            content: The memory content (text)
            memory_type: episodic|semantic|procedural|reflective|shared
            importance: How important this memory is (0-1)
            tags: Optional tags for classification
            metadata: Optional JSON metadata
            source_uri: Where this memory came from

        Returns:
            UUID of the stored memory
        """
        return self.store.store_memory(
            agent_id=self.agent_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            tags=tags,
            metadata=metadata,
            source_uri=source_uri,
        )

    # ── RECALL ───────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        k: int = 5,
        threshold: float = 0.6,
        memory_type: str | None = None,
        include_all_agents: bool = False,
    ) -> list[Memory]:
        """
        Semantic search for relevant memories.

        Args:
            query: Natural language query
            k: Number of results
            threshold: Minimum similarity (0-1)
            memory_type: Filter by type
            include_all_agents: If True, search across all agents (shared context)

        Returns:
            List of Memory objects ranked by relevance
        """
        agent_id = None if include_all_agents else self.agent_id
        result = self.store.search_similar(
            query=query,
            agent_id=agent_id,
            k=k,
            threshold=threshold,
            memory_type=memory_type,
        )
        return result.memories

    # ── REFLECT ──────────────────────────────────────────────────

    def reflect(self, topic: str, k: int = 10) -> str:
        """
        Retrieve related memories and format them as context.
        Returns a formatted string suitable for injection into agent prompts.

        Args:
            topic: Topic to reflect on
            k: Number of memories to retrieve

        Returns:
            Formatted string of relevant memories
        """
        memories = self.recall(topic, k=k, threshold=0.5)

        if not memories:
            return f"[No relevant memories found for: {topic}]"

        lines = [f"## Relevant Memories ({len(memories)} found)\n"]
        for i, mem in enumerate(memories, 1):
            sim_pct = f"{mem.similarity * 100:.0f}%" if mem.similarity else "?"
            lines.append(
                f"**{i}. [{mem.memory_type}] (similarity: {sim_pct})**\n"
                f"   {mem.content[:300]}{'...' if len(mem.content) > 300 else ''}\n"
                f"   _Agent: {mem.agent_id} | Importance: {mem.importance} | "
                f"Accessed: {mem.access_count}x_\n"
            )
        return "\n".join(lines)

    # ── KEYWORD RECALL ───────────────────────────────────────────

    def recall_keyword(self, query: str, k: int = 10) -> list[Memory]:
        """Full-text keyword search (faster than vector, use for exact term lookup)."""
        result = self.store.search_keyword(query=query, agent_id=self.agent_id, k=k)
        return result.memories

    # ── LINK ─────────────────────────────────────────────────────

    def link(
        self,
        source_id: UUID,
        target_id: UUID,
        link_type: str = "associates",
        weight: float = 1.0,
    ) -> UUID:
        """Create a link between two memories."""
        return self.store.link_memories(
            source_id=source_id,
            target_id=target_id,
            link_type=link_type,
            weight=weight,
            created_by=self.agent_id,
        )

    # ── SHARE ────────────────────────────────────────────────────

    def share(
        self,
        content: str,
        importance: float = 0.7,
        tags: list[str] | None = None,
        target_agents: list[str] | None = None,
    ) -> UUID:
        """
        Create a shared memory accessible by all agents (or specific targets).

        Use this at the end of pipeline steps to pass knowledge forward.

        Args:
            content: Knowledge to share
            importance: How important (shared defaults higher at 0.7)
            tags: Classification tags
            target_agents: Specific agents who should see this (None = all)

        Returns:
            UUID of the shared memory
        """
        from datetime import datetime, timezone
        meta = {
            "shared_by": self.agent_id,
            "target_agents": target_agents or ["all"],
            "shared_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.store.store_memory(
            agent_id=self.agent_id,
            content=f"[SHARED by {self.agent_id}] {content}",
            memory_type="shared",
            importance=importance,
            tags=["shared", self.agent_id, *(tags or [])],
            metadata=meta,
        )

    # ── RECALL SHARED ────────────────────────────────────────────

    def recall_shared(
        self,
        query: str,
        k: int = 3,
        threshold: float = 0.4,
    ) -> list[Memory]:
        """
        Recall shared memories from ANY agent.
        Useful for cross-agent knowledge discovery.
        """
        result = self.store.search_similar(
            query=query,
            agent_id=None,  # search all agents
            k=k,
            threshold=threshold,
            memory_type="shared",
        )
        return result.memories

    # ── RECALL WITH SHARED (Combined) ────────────────────────────

    def recall_with_shared(
        self,
        query: str,
        k: int = 3,
        threshold: float = 0.45,
    ) -> list[Memory]:
        """
        Combined recall: agent's own memories + shared memories from others.
        Results are merged and sorted by similarity. Deduplicates by ID.

        This is the premium recall method — use this in the supervisor for
        maximum context awareness.
        """
        # Get own memories
        own = self.recall(query, k=k, threshold=threshold)

        # Get shared from all agents
        shared = self.recall_shared(query, k=k, threshold=threshold)

        # Merge and deduplicate
        seen_ids = set()
        merged = []
        for m in own + shared:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                merged.append(m)

        # Sort by similarity descending, take top k
        merged.sort(key=lambda m: m.similarity or 0, reverse=True)
        return merged[:k]

    # ── STATS ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Get memory statistics for this agent."""
        return self.store.get_stats(agent_id=self.agent_id)
