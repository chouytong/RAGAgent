"""Preserve retry audit attempts while identifying the effective response.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("retry_of_message_id", sa.String(36), nullable=True))
    op.add_column(
        "messages", sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "messages", sa.Column("is_effective", sa.Boolean(), nullable=False, server_default="true")
    )
    op.create_foreign_key(
        "fk_messages_retry_of_message_id",
        "messages",
        "messages",
        ["retry_of_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint("ck_messages_attempt_number", "messages", "attempt_number >= 1")
    # Earlier versions retained lineage in metadata. Only link an earlier
    # assistant in the same conversation; malformed legacy metadata remains
    # intact for audit and cannot produce cross-conversation links or cycles.
    op.execute(
        """
        UPDATE messages AS retry
        SET retry_of_message_id = original.id
        FROM messages AS original
        WHERE retry.role = 'assistant' AND original.role = 'assistant'
          AND retry.metadata->>'retry_of_message_id' = original.id
          AND retry.conversation_id = original.conversation_id
          AND original.ordinal < retry.ordinal
        """
    )
    op.execute(
        """
        WITH RECURSIVE attempts(id, attempt_number) AS (
          SELECT id, 1 FROM messages WHERE retry_of_message_id IS NULL
          UNION ALL
          SELECT retry.id, original.attempt_number + 1
          FROM messages AS retry
          JOIN attempts AS original ON retry.retry_of_message_id = original.id
        )
        UPDATE messages AS message SET attempt_number = attempts.attempt_number
        FROM attempts WHERE message.id = attempts.id
        """
    )
    op.execute(
        """
        UPDATE messages AS original SET is_effective = false
        WHERE EXISTS (
          SELECT 1 FROM messages AS retry WHERE retry.retry_of_message_id = original.id
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_messages_attempt_number", "messages", type_="check")
    op.drop_constraint("fk_messages_retry_of_message_id", "messages", type_="foreignkey")
    op.drop_column("messages", "is_effective")
    op.drop_column("messages", "attempt_number")
    op.drop_column("messages", "retry_of_message_id")
