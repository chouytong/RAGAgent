"""Add local conversations, messages, summaries and explicit structured memory.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("mode IN ('rag', 'research')", name="ck_conversations_mode"),
    )
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])
    op.add_column("runs", sa.Column("conversation_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("client_request_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_runs_conversation_id",
        "runs",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_runs_conversation_id", "runs", ["conversation_id"])
    op.create_unique_constraint(
        "uq_runs_conversation_request", "runs", ["conversation_id", "client_request_id"]
    )
    op.create_index(
        "uq_runs_conversation_active",
        "runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("conversation_id IS NOT NULL AND status IN ('queued', 'running')"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("conversation_id", "ordinal", name="uq_messages_conversation_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', "
            "'insufficient_evidence', 'failed', 'cancelled')",
            name="ck_messages_status",
        ),
    )
    op.create_index("ix_messages_run_id", "messages", ["run_id"])
    op.create_index("ix_messages_conversation_order", "messages", ["conversation_id", "ordinal"])
    op.create_table(
        "conversation_summaries",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("through_ordinal", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("through_ordinal >= 0", name="ck_conversation_summaries_ordinal"),
        sa.CheckConstraint("version >= 1", name="ck_conversation_summaries_version"),
    )
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(128), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "kind IN ('goal', 'constraint', 'term', 'preference', 'task')",
            name="ck_conversation_memories_kind",
        ),
        sa.CheckConstraint(
            "filters IS NULL OR kind = 'constraint'", name="ck_conversation_memories_filters"
        ),
    )
    op.create_index(
        "ix_conversation_memories_conversation_created",
        "conversation_memories",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("conversation_memories")
    op.drop_table("conversation_summaries")
    op.drop_table("messages")
    op.drop_index("uq_runs_conversation_active", table_name="runs")
    op.drop_constraint("uq_runs_conversation_request", "runs", type_="unique")
    op.drop_index("ix_runs_conversation_id", table_name="runs")
    op.drop_constraint("fk_runs_conversation_id", "runs", type_="foreignkey")
    op.drop_column("runs", "client_request_id")
    op.drop_column("runs", "conversation_id")
    op.drop_table("conversations")
