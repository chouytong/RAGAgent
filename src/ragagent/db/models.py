import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ragagent.settings import get_settings


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint(
            "source_status IN ('unknown', 'active', 'withdrawn', 'retracted')",
            name="ck_papers_source_status",
        ),
        CheckConstraint(
            "arxiv_version IS NULL OR (arxiv_version > 0 AND arxiv_family_id IS NOT NULL)",
            name="ck_papers_arxiv_version",
        ),
        UniqueConstraint("arxiv_family_id", "arxiv_version", name="uq_papers_arxiv_version"),
        Index(
            "uq_papers_uploaded_sha256",
            "sha256",
            unique=True,
            postgresql_where=text("arxiv_id IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    venue: Mapped[str | None] = mapped_column(String(256), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    arxiv_family_id: Mapped[str | None] = mapped_column(String(64), index=True)
    arxiv_version: Mapped[int | None] = mapped_column(Integer)
    # This is a source annotation, independent of indexing/processing status.
    # Atom resolution alone cannot prove that a paper is active or not retracted.
    source_status: Mapped[Literal["unknown", "active", "withdrawn", "retracted"]] = mapped_column(
        String(32), default="unknown", server_default="unknown"
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(64))
    original_path: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256), unique=True)


class PaperAuthor(Base):
    __tablename__ = "paper_authors"
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str] = mapped_column(ForeignKey("authors.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("paper_id", "identity", name="uq_sections_paper_identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("sections.id"))
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    identity: Mapped[str] = mapped_column(Text, default=new_id)
    ordinal: Mapped[int] = mapped_column(Integer)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("page_start >= 1 AND page_end >= page_start"),
        CheckConstraint("token_count > 0"),
        Index("ix_chunks_search", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[str] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    section_path: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    element_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dimension))
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, content)", persisted=True)
    )


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("name", "entity_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)


class ChunkEntity(Base):
    __tablename__ = "chunk_entities"
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)


class EntityRelation(Base):
    __tablename__ = "entity_relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    relation: Mapped[str] = mapped_column(String(64))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))


class Dataset(Base):
    __tablename__ = "datasets"
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)


class Method(Base):
    __tablename__ = "methods"
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)


class Metric(Base):
    __tablename__ = "metrics"
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    unit: Mapped[str | None] = mapped_column(String(64))


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (CheckConstraint("span_start >= 0 AND span_end > span_start"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    span_start: Mapped[int] = mapped_column(Integer)
    span_end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    # Snapshot scores are heuristics, never probabilities.
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_runs_conversation_request"
        ),
        Index(
            "uq_runs_conversation_active",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "conversation_id IS NOT NULL AND status IN ('queued', 'running')"
            ),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    client_request_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(36), default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (CheckConstraint("mode IN ('rag', 'research')", name="ck_conversations_mode"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    mode: Mapped[Literal["rag", "research"]] = mapped_column(String(16))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "ordinal", name="uq_messages_conversation_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
        CheckConstraint("attempt_number >= 1", name="ck_messages_attempt_number"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', "
            "'insufficient_evidence', 'failed', 'cancelled')",
            name="ck_messages_status",
        ),
        Index("ix_messages_conversation_order", "conversation_id", "ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[Literal["user", "assistant", "system"]] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    ordinal: Mapped[int] = mapped_column(Integer)
    retry_of_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_effective: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[
        Literal["queued", "running", "completed", "insufficient_evidence", "failed", "cancelled"]
    ] = mapped_column(String(32), default="completed")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        CheckConstraint("through_ordinal >= 0", name="ck_conversation_summaries_ordinal"),
        CheckConstraint("version >= 1", name="ck_conversation_summaries_version"),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text)
    through_ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Memory(Base):
    __tablename__ = "conversation_memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('goal', 'constraint', 'term', 'preference', 'task')",
            name="ck_conversation_memories_kind",
        ),
        CheckConstraint(
            "filters IS NULL OR kind = 'constraint'", name="ck_conversation_memories_filters"
        ),
        Index("ix_conversation_memories_conversation_created", "conversation_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    kind: Mapped[Literal["goal", "constraint", "term", "preference", "task"]] = mapped_column(
        String(16)
    )
    key: Mapped[str | None] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    filters_json: Mapped[dict[str, Any] | None] = mapped_column("filters", JSONB(none_as_null=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
