import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    venue: Mapped[str | None] = mapped_column(String(256), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
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
    __table_args__ = (UniqueConstraint("paper_id", "path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("sections.id"))
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
