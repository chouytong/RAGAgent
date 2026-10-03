import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ragagent.api.papers import DB, QueueDep, enqueue
from ragagent.api.schemas import QueryRequest, ResearchRequest, RunResponse
from ragagent.db.models import ExecutionEvent, Run
from ragagent.db.session import session_factory

router = APIRouter(tags=["runs"])


@router.post("/api/rag/query", status_code=202)
def rag(request: QueryRequest, db: DB, queue: QueueDep) -> RunResponse:
    return RunResponse.model_validate(enqueue(db, queue, "rag", request.model_dump()))


@router.post("/api/research", status_code=202)
def research(request: ResearchRequest, db: DB, queue: QueueDep) -> RunResponse:
    return RunResponse.model_validate(enqueue(db, queue, "research", request.model_dump()))


@router.get("/api/runs/{run_id}")
@router.get("/api/research/{run_id}")
@router.get("/api/rag/{run_id}")
def status(run_id: str, db: DB) -> RunResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run_not_found")
    return RunResponse.model_validate(run)


async def stream_events(run_id: str, request: Request, cursor: int) -> AsyncIterator[str]:
    while not await request.is_disconnected():
        # Short-lived sessions do not hold a connection during SSE idle time.
        with session_factory()() as session:
            events = list(
                session.scalars(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.run_id == run_id, ExecutionEvent.id > cursor)
                    .order_by(ExecutionEvent.id)
                    .limit(100)
                )
            )
            run = session.get(Run, run_id)
            for event in events:
                cursor = event.id
                payload = json.dumps(
                    {
                        "node": event.node,
                        "payload": event.payload,
                        "time": event.created_at.isoformat(),
                    }
                )
                yield f"id: {event.id}\nevent: execution\ndata: {payload}\n\n"
            if run and run.status not in {"queued", "running"} and len(events) < 100:
                yield f"event: done\ndata: {RunResponse.model_validate(run).model_dump_json()}\n\n"
                return
        yield ": heartbeat\n\n"
        await asyncio.sleep(1)


@router.get("/api/runs/{run_id}/events")
@router.get("/api/research/{run_id}/events")
@router.get("/api/rag/{run_id}/events")
def events(
    run_id: str,
    request: Request,
    db: DB,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    if db.get(Run, run_id) is None:
        raise HTTPException(404, "run_not_found")
    try:
        cursor = int(last_event_id) if last_event_id else after
    except ValueError:
        raise HTTPException(422, "invalid_event_cursor") from None
    if cursor < 0:
        raise HTTPException(422, "invalid_event_cursor")
    db.rollback()  # Release the request session connection before the long-lived stream.
    return StreamingResponse(
        stream_events(run_id, request, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
