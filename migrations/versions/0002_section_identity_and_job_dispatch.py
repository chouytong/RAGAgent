"""Separate section identity from labels and persist job dispatch intent.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sections", sa.Column("identity", sa.Text(), nullable=True))
    # Existing chunks keep their section FK; lost historical occurrences are not invented.
    op.execute("UPDATE sections SET identity = 'legacy:' || id")
    op.alter_column("sections", "identity", nullable=False)
    op.drop_constraint("sections_paper_id_path_key", "sections", type_="unique")
    op.create_unique_constraint("uq_sections_paper_identity", "sections", ["paper_id", "identity"])
    op.create_table(
        "job_dispatches",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("attempts >= 0"),
    )
    op.create_index("ix_job_dispatches_dispatched_at", "job_dispatches", ["dispatched_at"])
    op.execute(
        "INSERT INTO job_dispatches "
        "(run_id, created_at, dispatched_at, claimed_at, attempts, last_error_code) "
        "SELECT id, created_at, "
        "CASE WHEN status = 'running' THEN created_at ELSE NULL END, "
        "CASE WHEN status = 'running' THEN CURRENT_TIMESTAMP ELSE NULL END, 0, NULL "
        "FROM runs WHERE status IN ('queued', 'running')"
    )


def downgrade() -> None:
    # Downgrading must never silently merge the new, distinct source sections.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM sections GROUP BY paper_id, path HAVING count(*) > 1) THEN "
        "RAISE EXCEPTION 'section_identity_downgrade_requires_unique_display_paths'; "
        "END IF; END $$"
    )
    op.drop_index("ix_job_dispatches_dispatched_at", table_name="job_dispatches")
    op.drop_table("job_dispatches")
    op.drop_constraint("uq_sections_paper_identity", "sections", type_="unique")
    op.create_unique_constraint("sections_paper_id_path_key", "sections", ["paper_id", "path"])
    op.drop_column("sections", "identity")
