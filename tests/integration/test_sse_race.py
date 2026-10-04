import asyncio
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent.api import runs
from ragagent.db.models import ExecutionEvent, Run


class ConnectedRequest:
    def __init__(self) -> None:
        self.polls = 0

    async def is_disconnected(self) -> bool:
        self.polls += 1
        assert self.polls < 10, "SSE failed to reach its terminal watermark"
        return False


@pytest.mark.integration
async def test_terminal_commit_between_stream_queries_does_not_lose_final_event(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with job_sessions() as session:
        run = Run(kind="rag", status="running", request={"query": "q"})
        session.add(run)
        session.commit()
        run_id = run.id
    interleaved = False

    class InterleavedSession:
        def __enter__(self) -> "InterleavedSession":
            self.session = job_sessions()
            return self

        def __exit__(self, *args: Any) -> None:
            self.session.close()

        def get(self, *args: Any) -> Any:
            return self.session.get(*args)

        def scalar(self, statement: Any) -> Any:
            return self.session.scalar(statement)

        def scalars(self, statement: Any) -> list[Any]:
            nonlocal interleaved
            old_batch = list(self.session.scalars(statement))
            if not interleaved:
                interleaved = True
                with job_sessions() as concurrent:
                    target = concurrent.get(Run, run_id)
                    target.status, target.result = "completed", {"answer": "answer"}
                    concurrent.add(
                        ExecutionEvent(
                            run_id=run_id, node="finished", payload={"status": "completed"}
                        )
                    )
                    concurrent.commit()
            return old_batch

    sleep = asyncio.sleep

    async def yield_now(seconds: float) -> None:
        await sleep(0)

    monkeypatch.setattr(runs, "session_factory", lambda: InterleavedSession)
    monkeypatch.setattr(runs.asyncio, "sleep", yield_now)
    try:
        messages = [message async for message in runs.stream_events(run_id, ConnectedRequest(), 0)]
        execution = [message for message in messages if "event: execution" in message]
        assert len(execution) == 1 and '"node": "finished"' in execution[0]
        assert "event: done" in messages[-1]
    finally:
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()


@pytest.mark.integration
async def test_terminal_replay_drains_multiple_pages_and_honors_cursor(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with job_sessions() as session:
        run = Run(kind="research", status="completed", request={"research_question": "q"})
        session.add(run)
        session.flush()
        session.add_all(
            [ExecutionEvent(run_id=run.id, node="step", payload={"ordinal": i}) for i in range(250)]
        )
        session.add(ExecutionEvent(run_id=run.id, node="finished", payload={"status": "completed"}))
        session.commit()
        run_id = run.id
        ids = list(
            session.scalars(
                select(ExecutionEvent.id)
                .where(ExecutionEvent.run_id == run_id)
                .order_by(ExecutionEvent.id)
            )
        )
    monkeypatch.setattr(runs, "session_factory", lambda: job_sessions)
    try:
        messages = [
            message async for message in runs.stream_events(run_id, ConnectedRequest(), ids[24])
        ]
        delivered = [
            int(message.splitlines()[0].split(": ")[1])
            for message in messages
            if message.startswith("id:")
        ]
        assert delivered == ids[25:]
        assert '"node": "finished"' in messages[-2]
        assert "event: done" in messages[-1]
    finally:
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()
