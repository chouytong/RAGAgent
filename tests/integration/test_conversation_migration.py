"""Upgrade legacy Runs without recreation; verify FK deletion and round trip."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_conversation_migration_legacy_run_and_cascades() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL migration")
    database_name = "ragagent_conversation_migration_" + uuid4().hex
    url = make_url(database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    probe = create_engine(url.set(database=database_name), hide_parameters=True)
    root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": url.set(database=database_name).render_as_string(hide_password=False),
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
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        migrate("upgrade", "0003")
        with probe.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO runs(id,kind,status,request,trace_id,created_at) "
                    "VALUES ('legacy','rag','completed','{\"query\":\"legacy question\"}',"
                    "'legacy',now())"
                )
            )
        migrate("upgrade", "head")
        migrate("check")
        with probe.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT conversation_id,client_request_id,request->>'query' "
                    "FROM runs WHERE id='legacy'"
                )
            ).one() == (None, None, "legacy question")
            connection.execute(
                text(
                    "INSERT INTO conversations(id,title,mode,metadata,created_at,updated_at) "
                    "VALUES ('conversation','Local','rag','{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO runs(id,conversation_id,client_request_id,kind,status,"
                    "request,trace_id,created_at) VALUES ('turn','conversation','request',"
                    "'rag','completed','{\"query\":\"private fixture\"}','trace',now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO messages(id,conversation_id,role,content,ordinal,run_id,"
                    "status,metadata,created_at,updated_at) VALUES "
                    "('user','conversation','user','private fixture',0,'turn','completed',"
                    "'{}',now(),now()),('assistant','conversation','assistant','verified answer',"
                    "1,'turn','completed','{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_summaries(conversation_id,content,through_ordinal,"
                    "version,metadata,created_at,updated_at) VALUES "
                    "('conversation','context only',1,1,'{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_memories(id,conversation_id,kind,content,metadata,"
                    "created_at,updated_at) VALUES ('memory','conversation','goal',"
                    "'compare methods','{}',now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO job_dispatches(run_id,created_at,attempts) VALUES ('turn',now(),1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO execution_events(run_id,node,payload,created_at) "
                    "VALUES ('turn','finished','{}',now())"
                )
            )
            connection.execute(text("DELETE FROM conversations WHERE id='conversation'"))
            for table in (
                "conversations",
                "messages",
                "conversation_summaries",
                "conversation_memories",
                "job_dispatches",
                "execution_events",
            ):
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
            assert connection.scalar(text("SELECT count(*) FROM runs WHERE id='turn'")) == 0
            assert connection.scalar(text("SELECT count(*) FROM runs WHERE id='legacy'")) == 1
        migrate("downgrade", "0003")
        with probe.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('conversations')")) is None
            assert (
                connection.scalar(text("SELECT request->>'query' FROM runs WHERE id='legacy'"))
                == "legacy question"
            )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        probe.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()
