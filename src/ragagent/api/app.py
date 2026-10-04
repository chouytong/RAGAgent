import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from rq import Queue, Worker
from sqlalchemy import text

from ragagent.api import evaluations, papers, providers, runs
from ragagent.api.dependencies import get_search
from ragagent.api.dispatcher import dispatcher_lifespan
from ragagent.api.papers import DB
from ragagent.api.queue import RQQueue
from ragagent.api.schemas import QueryRequest, SearchResponse
from ragagent.api.security import local_request_error
from ragagent.domain.research import QueryPlan
from ragagent.errors import ApplicationError
from ragagent.evaluation.artifacts import usage_delta, usage_snapshot
from ragagent.observability import configure_logging

app = FastAPI(title="Scientific RAGAgent", version="0.1.0", lifespan=dispatcher_lifespan)
app.include_router(papers.router)
app.include_router(runs.router)
app.include_router(providers.router)
app.include_router(evaluations.router)
logger = logging.getLogger("ragagent.api")


configure_logging()


@app.middleware("http")
async def trace(request: Request, call_next: Any) -> Any:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    try:
        error = local_request_error(request)
        response = (
            JSONResponse(status_code=403, content={"error_code": error, "request_id": request_id})
            if error
            else await call_next(request)
        )
    except Exception:
        logger.error(
            "http_failed", extra={"request_id": request_id, "error_code": "internal_error"}
        )
        response = JSONResponse(
            status_code=500, content={"error_code": "internal_error", "request_id": request_id}
        )
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request",
        extra={"request_id": request_id, "latency_ms": (time.perf_counter() - start) * 1000},
    )
    return response


@app.exception_handler(ApplicationError)
async def safe_error(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error_code": exc.code, "request_id": getattr(request.state, "request_id", None)},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready")
def ready(db: DB) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        connection = RQQueue().connection()
        connection.ping()
        workers = Worker.all(connection=connection, queue=Queue("research", connection=connection))
    except Exception:
        raise ApplicationError("infrastructure_unavailable") from None
    if not workers:
        raise ApplicationError("worker_unavailable")
    return {"status": "ready"}


@app.post("/api/search", response_model=SearchResponse)
async def search(request: QueryRequest, db: DB) -> SearchResponse | JSONResponse:
    retriever = get_search(db)
    embedder = getattr(getattr(retriever, "dense", None), "embedder", None)
    before = usage_snapshot(embedder)
    try:
        result = await retriever.search(QueryPlan(queries=[request.query], filters=request.filters))
        db.commit()
    except Exception as exc:
        db.rollback()
        return JSONResponse(
            status_code=503 if isinstance(exc, ApplicationError) else 500,
            content={
                "error_code": exc.code if isinstance(exc, ApplicationError) else "internal_error",
                "usage": {"embedding": usage_delta(before, usage_snapshot(embedder))},
                "usage_scope": "current_request",
            },
        )
    return SearchResponse(
        **result.model_dump(),
        usage={"embedding": usage_delta(before, usage_snapshot(embedder))},
    )
