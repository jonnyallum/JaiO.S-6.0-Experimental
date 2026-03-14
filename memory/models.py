"""
Pydantic models for the Memory Spine.
Provides type-safe data structures for memory operations.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class MemoryCreate(BaseModel):
    """Input model for creating a new memory."""
    agent_id: str
    content: str = Field(..., min_length=1, max_length=50000)
    memory_type: str = "episodic"
    summary: str | None = None
    metadata: dict = Field(default_factory=dict)
    source_uri: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    expires_at: datetime | None = None

    @field_validator("memory_type")
    @classmethod
    def validate_memory_type(cls, v):
        allowed = {"episodic", "semantic", "procedural", "reflective", "shared"}
        if v not in allowed:
            raise ValueError(f"memory_type must be one of {allowed}")
        return v

    @field_validator("importance")
    @classmethod
    def validate_importance(cls, v):
        if not 0 <= v <= 1:
            raise ValueError("importance must be between 0 and 1")
        return v


class Memory(BaseModel):
    """Full memory record returned from the database."""
    id: UUID
    agent_id: str
    content: str
    summary: str | None = None
    memory_type: str
    status: str
    confidence: float = 1.0
    importance: float = 0.5
    access_count: int = 0
    decay_factor: float = 1.0
    metadata: dict = Field(default_factory=dict)
    source_uri: str | None = None
    tags: list[str] = Field(default_factory=list)
    remembered_at: datetime | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Populated during search
    similarity: float | None = None


class MemoryChunkCreate(BaseModel):
    """Input model for creating a memory chunk."""
    memory_id: UUID
    chunk_index: int = Field(ge=0)
    content: str = Field(..., min_length=1)
    token_count: int = Field(default=0, ge=0)
    metadata: dict = Field(default_factory=dict)


class MemoryChunk(BaseModel):
    """Full memory chunk record."""
    id: UUID
    memory_id: UUID
    chunk_index: int
    content: str
    token_count: int = 0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    similarity: float | None = None


class MemoryLinkCreate(BaseModel):
    """Input model for linking two memories."""
    source_memory_id: UUID
    target_memory_id: UUID
    link_type: str
    weight: float = 1.0
    created_by_agent: str
    metadata: dict = Field(default_factory=dict)

    @field_validator("link_type")
    @classmethod
    def validate_link_type(cls, v):
        allowed = {"causal", "temporal", "contradicts", "supports", "refines", "supersedes", "associates"}
        if v not in allowed:
            raise ValueError(f"link_type must be one of {allowed}")
        return v


class MemoryLink(BaseModel):
    """Full memory link record."""
    id: UUID
    source_memory_id: UUID
    target_memory_id: UUID
    link_type: str
    weight: float = 1.0
    created_by_agent: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class SearchResult(BaseModel):
    """Result of a memory search operation."""
    memories: list[Memory]
    query: str
    agent_id: str | None = None
    k: int
    search_type: str = "vector"  # vector, keyword, hybrid
    elapsed_ms: float = 0.0
