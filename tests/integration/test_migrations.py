"""Upgrade real legacy data and reject a lossy section downgrade."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from ragagent.settings import get_settings


@pytest.mark.integration
def test_legacy_upgrade_and_lossless_downgrade() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL migrations")
    database_name = "ragagent_migration_" + uuid.uuid4().hex
    url = make_url(database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    probe = create_engine(url.set(database=database_name), hide_parameters=True)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    current_head = ScriptDirectory.from_config(config).get_current_head()
    assert current_head is not None
    environment = {
        **os.environ,
        "DATABASE_URL": url.set(database=database_name).render_as_string(hide_password=False),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root / "src"),
    }

    def alembic(*arguments: str, succeeds: bool = True) -> str:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = result.stdout + result.stderr
        assert (result.returncode == 0) == succeeds, output
        return output

    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        alembic("upgrade", "0001")
        with probe.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO papers (id,title,sha256,status,original_path,created_at) "
                    "VALUES ('legacy-paper','Legacy fixture',:sha,'indexed','/fixture.pdf',now())"
                ),
                {"sha": "a" * 64},
            )
            for source_id, arxiv_id, checksum in [
                ("legacy-unversioned", "2408.09869", "b" * 64),
                ("legacy-pinned", "2408.12345v2", "c" * 64),
            ]:
                connection.execute(
                    text(
                        "INSERT INTO papers "
                        "(id,title,arxiv_id,sha256,status,original_path,created_at) "
                        "VALUES (:id,'Legacy arXiv',:arxiv_id,:sha,'indexed','/source.pdf',now())"
                    ),
                    {"id": source_id, "arxiv_id": arxiv_id, "sha": checksum},
                )
            connection.execute(
                text(
                    "INSERT INTO sections (id,paper_id,parent_id,title,path,ordinal) VALUES "
                    "('legacy-parent','legacy-paper',NULL,'Methods','Methods',0), "
                    "('legacy-child','legacy-paper','legacy-parent','Training',"
                    "'Methods / Training',1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO chunks "
                    "(id,paper_id,section_id,section_path,page_start,page_end,element_type,"
                    "content,token_count,ordinal,metadata,embedding) VALUES "
                    "('legacy-chunk','legacy-paper','legacy-child','Methods / Training',2,3,"
                    "'text','Legacy contrastive training source.',5,0,'{}',CAST(:vector AS vector))"
                ),
                {"vector": str([1.0] + [0.0] * (get_settings().embedding_dimension - 1))},
            )
            for status in ("queued", "running", "completed"):
                connection.execute(
                    text(
                        "INSERT INTO runs (id,kind,status,request,trace_id,created_at) "
                        "VALUES (:id,'rag',:status,'{}',:id,now())"
                    ),
                    {"id": "legacy-" + status, "status": status},
                )
        alembic("upgrade", "head")
        alembic("check")
        with probe.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT arxiv_family_id,arxiv_version,source_status "
                    "FROM papers WHERE id='legacy-unversioned'"
                )
            ).one() == ("2408.09869", None, "unknown")
            assert connection.execute(
                text(
                    "SELECT arxiv_family_id,arxiv_version,source_status "
                    "FROM papers WHERE id='legacy-pinned'"
                )
            ).one() == ("2408.12345", 2, "unknown")
            # Separate source identities may share byte-identical PDFs.
            connection.execute(
                text("UPDATE papers SET sha256=:sha WHERE id='legacy-pinned'"),
                {"sha": "b" * 64},
            )
        failure = alembic("downgrade", "0002", succeeds=False)
        assert "source_identity_downgrade_requires_unique_checksums" in failure
        with probe.begin() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == current_head
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='messages' "
                        "AND column_name IN ('retry_of_message_id','attempt_number','is_effective')"
                    )
                )
                == 3
            )
            connection.execute(
                text("UPDATE papers SET sha256=:sha WHERE id='legacy-pinned'"),
                {"sha": "c" * 64},
            )
        with probe.begin() as connection:
            assert connection.execute(
                text("SELECT identity,parent_id FROM sections WHERE id='legacy-child'")
            ).one() == ("legacy:legacy-child", "legacy-parent")
            assert (
                connection.scalar(text("SELECT section_id FROM chunks WHERE id='legacy-chunk'"))
                == "legacy-child"
            )
            rows = connection.execute(
                text(
                    "SELECT run_id,dispatched_at IS NOT NULL,claimed_at IS NOT NULL "
                    "FROM job_dispatches ORDER BY run_id"
                )
            ).all()
            assert rows == [("legacy-queued", False, False), ("legacy-running", True, True)]
            connection.execute(
                text(
                    "INSERT INTO sections (id,paper_id,title,path,identity,ordinal) "
                    "VALUES ('new-occurrence','legacy-paper','Training','Methods / Training',"
                    "'new-occurrence',2)"
                )
            )
        failure = alembic("downgrade", "0001", succeeds=False)
        assert "section_identity_downgrade_requires_unique_display_paths" in failure
        with probe.begin() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == current_head
            )
            assert connection.scalar(text("SELECT count(*) FROM job_dispatches")) == 2
            assert connection.scalar(text("SELECT count(*) FROM sections")) == 3
            connection.execute(text("DELETE FROM sections WHERE id='new-occurrence'"))
        alembic("downgrade", "0001")
        with probe.connect() as connection:
            assert (
                connection.scalar(text("SELECT section_id FROM chunks WHERE id='legacy-chunk'"))
                == "legacy-child"
            )
            assert connection.scalar(text("SELECT to_regclass('job_dispatches')")) is None
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='sections' AND column_name='identity'"
                    )
                )
                == 0
            )
        alembic("downgrade", "base")
        alembic("upgrade", "head")
    finally:
        probe.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()
