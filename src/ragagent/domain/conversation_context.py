"""Typed local conversation-context contracts; never scientific evidence."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ragagent.domain.research import MetadataFilter


class ContextConfig(BaseModel):
    recent_message_limit: int = Field(default=8, ge=2, le=64)
    max_context_tokens: int = Field(default=8192, ge=4096, le=65536)
    summary_max_bytes: int = Field(default=2048, ge=256, le=16384)
    per_message_max_bytes: int = Field(default=2048, ge=256, le=16384)


class ContextMessage(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ordinal: int = Field(ge=0)
    role: Literal["user", "assistant", "system"]
    content: str
    status: str = "completed"


class StructuredMemory(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["goal", "constraint", "term", "preference", "task"]
    key: str | None = None
    content: str
    filters: MetadataFilter | None = None


class RollingSummary(BaseModel):
    content: str = ""
    through_ordinal: int = Field(default=-1, ge=-1)
    version: int = Field(default=0, ge=0)
    source_message_ids: list[str] = Field(default_factory=list)


class ResolvedReferent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mention: str = Field(min_length=1, max_length=256)
    resolved_text: str = Field(min_length=1, max_length=256)
    source_kind: Literal["message", "memory"]
    source_id: str = Field(min_length=1, max_length=64)


class QueryContextualization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["resolved", "ambiguous"]
    contextualized_query: str = Field(default="", max_length=10000)
    used_message_ids: list[str] = Field(default_factory=list, max_length=50)
    used_memory_ids: list[str] = Field(default_factory=list, max_length=100)
    referents: list[ResolvedReferent] = Field(default_factory=list, max_length=20)


class ContextBundle(BaseModel):
    original_query: str
    payload: dict[str, Any]
    summary: RollingSummary
    filters: MetadataFilter
    metadata: dict[str, Any]
    # Retain only text actually sent to the contextualizer, not the full history.
    message_sources: dict[str, str] = Field(default_factory=dict)
    memory_sources: dict[str, str] = Field(default_factory=dict)
    has_context: bool


class ResolvedContext(BaseModel):
    original_query: str
    contextualized_query: str
    filters: MetadataFilter
    metadata: dict[str, Any]
