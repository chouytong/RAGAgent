"""Upgrade persisted legacy retries without discarding messages or audit data."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_retry_lineage_migration_preserves_and_backfills_legacy_attempts() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL migration")
    name = "retry_migration_" + uuid4().hex
    url = make_url(database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    probe = create_engine(url.set(database=name), hide_parameters=True)
    root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": url.set(database=name).render_as_string(hide_password=False),
        "PYTHONPATH": str(root / "src"),
    }

    def migrate(*arguments: str) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        migrate("upgrade", "0004")
        with probe.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversations(id,title,mode,metadata,created_at,updated_at) "
                    "VALUES ('c','Keep','rag','{}',now(),now()), "
                    "('other','Separate','rag','{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO messages(id,conversation_id,role,content,ordinal,status,metadata,"
                    "created_at,updated_at) VALUES "
                    "('u','c','user','Original question',0,'completed','{}',now(),now()),"
                    "('a1','c','assistant','First refusal',1,'insufficient_evidence',"
                    "'{}',now(),now()),"
                    "('a2','c','assistant','Failed retry',2,'failed',"
                    '\'{"retry_of_message_id":"a1"}\',now(),now()),'
                    "('a3','c','assistant','Accepted retry',3,'completed',"
                    '\'{"retry_of_message_id":"a2"}\',now(),now()),'
                    "('foreign','other','assistant','Separate answer',0,'completed',"
                    "'{}',now(),now()),"
                    "('invalid','c','assistant','Preserve bad legacy metadata',4,'failed',"
                    '\'{"retry_of_message_id":"foreign"}\',now(),now())'
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_summaries(conversation_id,content,through_ordinal,"
                    "version,metadata,created_at,updated_at) VALUES "
                    "('c','Unverified saved excerpt',1,1,'{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_memories(id,conversation_id,kind,content,metadata,"
                    "created_at,updated_at) VALUES "
                    "('m','c','goal','Keep this goal','{}',now(),now())"
                )
            )
        migrate("upgrade", "head")
        migrate("check")
        with probe.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id,retry_of_message_id,attempt_number,is_effective,content "
                    "FROM messages WHERE conversation_id='c' ORDER BY ordinal"
                )
            ).all()
            assert rows == [
                ("u", None, 1, True, "Original question"),
                ("a1", None, 1, False, "First refusal"),
                ("a2", "a1", 2, False, "Failed retry"),
                ("a3", "a2", 3, True, "Accepted retry"),
                ("invalid", None, 1, True, "Preserve bad legacy metadata"),
            ]
            assert connection.scalar(text("SELECT count(*) FROM messages")) == 6
            assert (
                connection.scalar(text("SELECT content FROM conversation_summaries"))
                == "Unverified saved excerpt"
            )
            assert (
                connection.scalar(text("SELECT content FROM conversation_memories"))
                == "Keep this goal"
            )
            assert (
                connection.scalar(
                    text("SELECT metadata->>'retry_of_message_id' FROM messages WHERE id='invalid'")
                )
                == "foreign"
            )
        migrate("downgrade", "0004")
        with probe.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM messages")) == 6
            assert (
                connection.scalar(
                    text("SELECT metadata->>'retry_of_message_id' FROM messages WHERE id='a2'")
                )
                == "a1"
            )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        probe.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
