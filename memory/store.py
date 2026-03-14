"""
MemoryStore — Core CRUD and vector search for the Memory Spine.

This is the primary interface agents use to interact with persistent memory.
Handles storage, retrieval, similarity search, linking, and decay.

Usage:
    from memory.store import MemoryStore
    store = MemoryStore()
    mid = store.store_memory("hugo", "Discovered a new API pattern...", "semantic")
    results = store.search_similar("API patterns", agent_id="hugo", k=5)
"""
import hashlib
import json
import logging
import time
from uuid import UUID
from datetime import datetime, timezone, timedelta

from memory.connection import db_connection
from memory.embedding import get_embedding
from memory.models import (
    Memory, MemoryCreate,
    MemoryLink, MemoryLinkCreate, SearchResult,
)

log = logging.getLogger(__name__)

# ── Shared SQL fragments ─────────────────────────────────────────────────────
_MEMORY_COLUMNS = """
    id, agent_id, content, summary, memory_type, status,
    confidence, importance, access_count, decay_factor,
    metadata, source_uri, tags, remembered_at,
    last_accessed_at, created_at, updated_at
""".strip()


class MemoryStore:
    """
    Core memory storage and retrieval engine.
    All operations are synchronous (agents run in sync context).
    """

    # ── HELPERS (dedup) ───────────────────────────────────────────

    @staticmethod
    def _content_hash(agent_id: str, content: str) -> str:
        """Deterministic hash of agent+content for dedup checks."""
        normalised = f"{agent_id}::{content.strip().lower()}"
        return hashlib.md5(normalised.encode("utf-8")).hexdigest()

    def _find_duplicate(self, agent_id: str, content_hash: str) -> UUID | None:
        """Check if an active memory with this hash already exists."""
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM memories
                    WHERE agent_id = %s
                      AND status = 'active'
                      AND deleted_at IS NULL
                      AND metadata->>'content_hash' = %s
                    LIMIT 1
                    """,
                    (agent_id, content_hash),
                )
                row = cur.fetchone()
                if row:
                    # Bump access count on the existing memory
                    cur.execute(
                        "UPDATE memories SET access_count = access_count + 1, "
                        "last_accessed_at = NOW() WHERE id = %s",
                        (str(row[0]),),
                    )
                    return row[0]
        return None

    # ── STORE ────────────────────────────────────────────────────

    def store_memory(
        self,
        agent_id: str,
        content: str,
        memory_type: str = "episodic",
        summary: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
        source_uri: str | None = None,
        generate_embedding: bool = True,
    ) -> UUID:
        """
        Store a new memory with optional embedding generation.
        Automatically deduplicates: if an identical memory exists for this
        agent, bumps its access_count and returns the existing ID.

        Returns:
            UUID of the created (or existing) memory
        """
        # ── Dedup check ──────────────────────────────────────────
        content_hash = self._content_hash(agent_id, content)
        existing_id = self._find_duplicate(agent_id, content_hash)
        if existing_id:
            log.info(f"memory.dedup_hit id={existing_id} agent={agent_id}")
            return existing_id

        mem = MemoryCreate(
            agent_id=agent_id,
            content=content,
            memory_type=memory_type,
            summary=summary,
            metadata={**(metadata or {}), "content_hash": content_hash},
            tags=tags or [],
            importance=importance,
            source_uri=source_uri,
        )

        # Generate embedding
        embedding = None
        status = "pending_embed"
        if generate_embedding:
            try:
                embedding = get_embedding(content)
                status = "active"
            except Exception as e:
                log.warning(f"memory.embed_failed agent={agent_id} error={e}")

        meta_json = json.dumps(mem.metadata)

        with db_connection() as conn:
            with conn.cursor() as cur:
                if embedding:
                    cur.execute(
                        """
                        INSERT INTO memories (
                            agent_id, content, summary, memory_type, status,
                            embedding, importance, metadata, source_uri, tags
                        ) VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s::jsonb, %s, %s)
                        RETURNING id
                        """,
                        (
                            mem.agent_id, mem.content, mem.summary,
                            mem.memory_type, status,
                            str(embedding), mem.importance,
                            meta_json, mem.source_uri, mem.tags,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO memories (
                            agent_id, content, summary, memory_type, status,
                            importance, metadata, source_uri, tags
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        RETURNING id
                        """,
                        (
                            mem.agent_id, mem.content, mem.summary,
                            mem.memory_type, status,
                            mem.importance, meta_json,
                            mem.source_uri, mem.tags,
                        ),
                    )
                memory_id = cur.fetchone()[0]

        log.info(f"memory.stored id={memory_id} agent={agent_id} type={memory_type} status={status}")
        return memory_id

    # ── RETRIEVE ─────────────────────────────────────────────────

    def get_memory(self, memory_id: UUID) -> Memory | None:
        """Retrieve a single memory by ID."""
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = %s AND deleted_at IS NULL",
                    (str(memory_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None

                # Bump access count atomically
                cur.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed_at = NOW() WHERE id = %s",
                    (str(memory_id),),
                )
                return self._row_to_memory(row)

    def get_agent_memories(
        self,
        agent_id: str,
        memory_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Memory]:
        """Get all memories for an agent, optionally filtered by type."""
        type_filter = "AND memory_type = %s" if memory_type else ""

        with db_connection() as conn:
            with conn.cursor() as cur:
                sql = f"""
                    SELECT {_MEMORY_COLUMNS}
                    FROM memories
                    WHERE agent_id = %s {type_filter}
                      AND status = 'active' AND deleted_at IS NULL
                    ORDER BY importance DESC, created_at DESC
                    LIMIT %s OFFSET %s
                """
                params: list = [agent_id]
                if memory_type:
                    params.append(memory_type)
                params.extend([limit, offset])

                cur.execute(sql, tuple(params))
                return [self._row_to_memory(r) for r in cur.fetchall()]

    # ── SEARCH (VECTOR SIMILARITY) ───────────────────────────────

    def search_similar(
        self,
        query: str,
        agent_id: str | None = None,
        k: int = 5,
        threshold: float = 0.7,
        memory_type: str | None = None,
        tags: list[str] | None = None,
    ) -> SearchResult:
        """
        Semantic similarity search using pgvector cosine distance.

        Args:
            query: Natural language query
            agent_id: Filter to specific agent (None = search all)
            k: Number of results
            threshold: Minimum similarity score (0-1)
            memory_type: Filter by memory type
            tags: Filter by tags (any match)

        Returns:
            SearchResult with ranked memories
        """
        start = time.time()
        query_vec = str(get_embedding(query))

        # Build optional filters
        agent_filter = "AND agent_id = %s" if agent_id else ""
        type_filter = "AND memory_type = %s" if memory_type else ""
        tag_filter = "AND tags && %s" if tags else ""

        sql = f"""
            SELECT {_MEMORY_COLUMNS},
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE status = 'active'
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
              {agent_filter}
              {type_filter}
              {tag_filter}
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params: list = [query_vec]
        if agent_id:
            params.append(agent_id)
        if memory_type:
            params.append(memory_type)
        if tags:
            params.append(tags)
        params.extend([query_vec, threshold, query_vec, k])

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

        elapsed_ms = round((time.time() - start) * 1000, 1)
        memories = []
        for row in rows:
            mem = self._row_to_memory(row[:17])
            mem.similarity = float(row[17])
            memories.append(mem)

        log.info(
            f"memory.search query_len={len(query)} results={len(memories)} "
            f"elapsed_ms={elapsed_ms} agent={agent_id}"
        )
        return SearchResult(
            memories=memories, query=query, agent_id=agent_id,
            k=k, search_type="vector", elapsed_ms=elapsed_ms,
        )

    # ── KEYWORD SEARCH (Full-Text) ───────────────────────────────

    def search_keyword(
        self,
        query: str,
        agent_id: str | None = None,
        k: int = 10,
    ) -> SearchResult:
        """Full-text search using PostgreSQL tsvector."""
        start = time.time()
        agent_filter = "AND agent_id = %s" if agent_id else ""

        sql = f"""
            SELECT {_MEMORY_COLUMNS},
                   ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
            FROM memories
            WHERE search_vector @@ plainto_tsquery('english', %s)
              AND status = 'active' AND deleted_at IS NULL
              {agent_filter}
            ORDER BY rank DESC
            LIMIT %s
        """
        params: list = [query, query]
        if agent_id:
            params.append(agent_id)
        params.append(k)

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

        elapsed_ms = round((time.time() - start) * 1000, 1)
        memories = []
        for row in rows:
            mem = self._row_to_memory(row[:17])
            mem.similarity = float(row[17])
            memories.append(mem)

        return SearchResult(
            memories=memories, query=query, agent_id=agent_id,
            k=k, search_type="keyword", elapsed_ms=elapsed_ms,
        )

    # ── LINKS ────────────────────────────────────────────────────

    def link_memories(
        self,
        source_id: UUID,
        target_id: UUID,
        link_type: str,
        weight: float = 1.0,
        created_by: str = "system",
        metadata: dict | None = None,
    ) -> UUID:
        """Create an associative link between two memories."""
        link = MemoryLinkCreate(
            source_memory_id=source_id,
            target_memory_id=target_id,
            link_type=link_type,
            weight=weight,
            created_by_agent=created_by,
            metadata=metadata or {},
        )

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memory_links (
                        source_memory_id, target_memory_id, link_type,
                        weight, created_by_agent, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (source_memory_id, target_memory_id, link_type)
                    DO UPDATE SET weight = EXCLUDED.weight, metadata = EXCLUDED.metadata
                    RETURNING id
                    """,
                    (
                        str(link.source_memory_id), str(link.target_memory_id),
                        link.link_type, link.weight, link.created_by_agent,
                        json.dumps(link.metadata),
                    ),
                )
                link_id = cur.fetchone()[0]

        log.info(f"memory.linked source={source_id} target={target_id} type={link_type}")
        return link_id

    def get_linked_memories(
        self, memory_id: UUID, link_type: str | None = None, direction: str = "outbound",
    ) -> list[tuple[Memory, MemoryLink]]:
        """Get memories linked to/from the given memory."""
        if direction == "outbound":
            id_col, join_col = "source_memory_id", "target_memory_id"
        else:
            id_col, join_col = "target_memory_id", "source_memory_id"

        type_filter = "AND ml.link_type = %s" if link_type else ""

        sql = f"""
            SELECT m.id, m.agent_id, m.content, m.summary, m.memory_type, m.status,
                   m.confidence, m.importance, m.access_count, m.decay_factor,
                   m.metadata, m.source_uri, m.tags, m.remembered_at,
                   m.last_accessed_at, m.created_at, m.updated_at,
                   ml.id, ml.link_type, ml.weight, ml.created_by_agent
            FROM memory_links ml
            JOIN memories m ON m.id = ml.{join_col}
            WHERE ml.{id_col} = %s AND m.deleted_at IS NULL
            {type_filter}
            ORDER BY ml.weight DESC
        """
        params: list = [str(memory_id)]
        if link_type:
            params.append(link_type)

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

        results = []
        for row in rows:
            mem = self._row_to_memory(row[:17])
            link = MemoryLink(
                id=row[17],
                source_memory_id=memory_id if direction == "outbound" else mem.id,
                target_memory_id=mem.id if direction == "outbound" else memory_id,
                link_type=row[18],
                weight=float(row[19]),
                created_by_agent=row[20],
            )
            results.append((mem, link))
        return results

    # ── DECAY ────────────────────────────────────────────────────

    def decay_stale_memories(
        self, agent_id: str | None = None, older_than_days: int = 30,
    ) -> int:
        """
        Apply memory decay to old, infrequently accessed memories.
        Reduces decay_factor by 10% for stale memories.
        Archives memories that decay below 0.1.

        Returns: number of memories decayed
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        agent_filter = "AND agent_id = %s" if agent_id else ""

        with db_connection() as conn:
            with conn.cursor() as cur:
                # Decay stale memories
                decay_params: list = [cutoff]
                if agent_id:
                    decay_params.append(agent_id)

                cur.execute(
                    f"""
                    UPDATE memories
                    SET decay_factor = GREATEST(decay_factor * 0.9, 0.01),
                        updated_at = NOW()
                    WHERE last_accessed_at < %s
                      AND status = 'active' AND deleted_at IS NULL
                      {agent_filter}
                    """,
                    tuple(decay_params),
                )
                decayed = cur.rowcount

                # Archive heavily decayed memories
                archive_params: list = []
                if agent_id:
                    archive_params.append(agent_id)

                cur.execute(
                    f"""
                    UPDATE memories
                    SET status = 'archived', updated_at = NOW()
                    WHERE decay_factor < 0.1
                      AND status = 'active' AND deleted_at IS NULL
                      {agent_filter}
                    """,
                    tuple(archive_params) if archive_params else None,
                )
                archived = cur.rowcount

        log.info(f"memory.decay decayed={decayed} archived={archived} agent={agent_id}")
        return decayed

    # ── STATS ────────────────────────────────────────────────────

    def get_stats(self, agent_id: str | None = None) -> dict:
        """Get memory statistics for an agent or the whole system."""
        if agent_id:
            where = "WHERE agent_id = %s AND deleted_at IS NULL"
            params: tuple = (agent_id,)
        else:
            where = "WHERE deleted_at IS NULL"
            params = ()

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE status = 'active') AS active,
                        COUNT(*) FILTER (WHERE status = 'archived') AS archived,
                        COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
                        COUNT(DISTINCT agent_id) AS unique_agents,
                        AVG(importance) AS avg_importance,
                        AVG(decay_factor) AS avg_decay
                    FROM memories
                    {where}
                    """,
                    params,
                )
                row = cur.fetchone()
                return {
                    "total": row[0],
                    "active": row[1],
                    "archived": row[2],
                    "embedded": row[3],
                    "unique_agents": row[4],
                    "avg_importance": round(float(row[5]), 3) if row[5] else 0,
                    "avg_decay": round(float(row[6]), 3) if row[6] else 0,
                }

    # ── HELPERS ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_memory(row: tuple) -> Memory:
        """Convert a database row tuple to a Memory model."""
        return Memory(
            id=row[0],
            agent_id=row[1],
            content=row[2],
            summary=row[3],
            memory_type=row[4],
            status=row[5],
            confidence=float(row[6]) if row[6] else 1.0,
            importance=float(row[7]) if row[7] else 0.5,
            access_count=row[8] or 0,
            decay_factor=float(row[9]) if row[9] else 1.0,
            metadata=row[10] or {},
            source_uri=row[11],
            tags=row[12] or [],
            remembered_at=row[13],
            last_accessed_at=row[14],
            created_at=row[15],
            updated_at=row[16],
        )
