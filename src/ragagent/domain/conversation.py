"""Conversation lifecycle contracts. Conversation context never defines Evidence."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ragagent.domain.privacy import safe_text
from ragagent.domain.research import MetadataFilter

ConversationMode = Literal["rag", "research"]
MessageRole = Literal["user", "assistant", "system"]
MessageStatus = Literal[
    "queued", "running", "completed", "insufficient_evidence", "failed", "cancelled"
]
MemoryKind = Literal["goal", "constraint", "term", "preference", "task"]


def safe_context_text(value: str) -> str:
    return safe_text(value)


class ConversationFilters(MetadataFilter):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "paper_ids",
        "authors",
        "venues",
        "sections",
        "entity_types",
        "datasets",
        "methods",
        "metrics",
    )
    @classmethod
    def clean_filter_text(cls, values: list[str]) -> list[str]:
        # These values are persisted in Run requests and explicit memories and
        # subsequently sent to the contextualizer. Their scientific field names
        # do not make arbitrary strings safe to store or transmit.
        return [safe_context_text(value) for value in values]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ConversationMode = "rag"
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        return safe_context_text(value) if isinstance(value, str) else value


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return safe_context_text(value) if isinstance(value, str) else value


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=10000)
    client_request_id: UUID
    filters: ConversationFilters = Field(default_factory=ConversationFilters)

    @field_validator("content", mode="before")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return safe_context_text(value) if isinstance(value, str) else value


class RetryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: MemoryKind
    key: str | None = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    filters: ConversationFilters | None = None

    @field_validator("content", "key", mode="before")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return safe_context_text(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def constraint_filters_only(self) -> "MemoryCreate":
        if self.filters is not None and self.kind != "constraint":
            raise ValueError("memory_filters_require_constraint")
        return self


class RunSummary(BaseModel):
    """Small execution identity/status; full results are fetched from the Run API."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    status: str
    trace_id: str
    error_code: str | None = None
    created_at: datetime


class RunSnapshot(RunSummary):
    """Compatibility detail contract; conversation endpoints use RunSummary."""

    result: dict[str, Any] | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    mode: ConversationMode
    archived: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    active_run_id: str | None = None


class MessageSummary(BaseModel):
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    ordinal: int
    retry_of_message_id: str | None = None
    attempt_number: int = Field(default=1, ge=1)
    is_effective: bool = True
    run_id: str | None
    status: MessageStatus
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    run: RunSummary | None = None


class MessageResponse(MessageSummary):
    """Compatibility import for callers migrating to MessageSummary."""


class TurnResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageSummary
    assistant_message: MessageSummary
    run: RunSummary


class SummaryResponse(BaseModel):
    conversation_id: str
    content: str
    through_ordinal: int
    version: int
    source_message_ids: list[str]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MemoryResponse(BaseModel):
    id: str
    conversation_id: str
    kind: MemoryKind
    key: str | None
    content: str
    filters: MetadataFilter | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
