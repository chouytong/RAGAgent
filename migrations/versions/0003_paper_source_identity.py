"""Freeze arXiv versions and retain explicitly annotated source status.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("arxiv_family_id", sa.String(64), nullable=True))
    op.add_column("papers", sa.Column("arxiv_version", sa.Integer(), nullable=True))
    op.add_column(
        "papers",
        sa.Column("source_status", sa.String(32), nullable=False, server_default="unknown"),
    )
    # Legacy unversioned imports cannot be assigned a version from their ID or
    # hash: retain unknown version rather than inventing a historical identity.
    op.execute(
        "UPDATE papers SET arxiv_family_id = regexp_replace(arxiv_id, 'v[1-9][0-9]*$', ''), "
        "arxiv_version = CASE WHEN arxiv_id ~ 'v[1-9][0-9]*$' "
        "THEN substring(arxiv_id FROM 'v([1-9][0-9]*)$')::integer ELSE NULL END "
        "WHERE arxiv_id IS NOT NULL"
    )
    op.create_index("ix_papers_arxiv_family_id", "papers", ["arxiv_family_id"])
    op.create_check_constraint(
        "ck_papers_source_status",
        "papers",
        "source_status IN ('unknown', 'active', 'withdrawn', 'retracted')",
    )
    op.create_check_constraint(
        "ck_papers_arxiv_version",
        "papers",
        "arxiv_version IS NULL OR (arxiv_version > 0 AND arxiv_family_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_papers_arxiv_version", "papers", ["arxiv_family_id", "arxiv_version"]
    )
    # Two versions may contain identical PDF bytes but still have distinct source
    # identities. Uploaded PDFs keep their original checksum deduplication.
    op.drop_constraint("papers_sha256_key", "papers", type_="unique")
    op.create_index("ix_papers_sha256", "papers", ["sha256"])
    op.create_index(
        "uq_papers_uploaded_sha256",
        "papers",
        ["sha256"],
        unique=True,
        postgresql_where=sa.text("arxiv_id IS NULL"),
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM papers GROUP BY sha256 HAVING count(*) > 1) THEN "
        "RAISE EXCEPTION 'source_identity_downgrade_requires_unique_checksums'; "
        "END IF; END $$"
    )
    op.drop_index("uq_papers_uploaded_sha256", table_name="papers")
    op.drop_index("ix_papers_sha256", table_name="papers")
    op.create_unique_constraint("papers_sha256_key", "papers", ["sha256"])
    op.drop_constraint("uq_papers_arxiv_version", "papers", type_="unique")
    op.drop_constraint("ck_papers_arxiv_version", "papers", type_="check")
    op.drop_constraint("ck_papers_source_status", "papers", type_="check")
    op.drop_index("ix_papers_arxiv_family_id", table_name="papers")
    op.drop_column("papers", "source_status")
    op.drop_column("papers", "arxiv_version")
    op.drop_column("papers", "arxiv_family_id")
