import logging
import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from rq import Queue, Worker
from sqlalchemy import text
from starlette.exceptions import HTTPException

from ragagent import __version__
from ragagent.api import auth, conversations, diagnostics, evaluations, papers, providers, runs
from ragagent.api.dependencies import get_search
from ragagent.api.dispatcher import dispatcher_lifespan
from ragagent.api.papers import DB
from ragagent.api.queue import RQQueue
from ragagent.api.schemas import QueryRequest, SearchResponse
from ragagent.api.security import local_request_error
from ragagent.domain.research import QueryPlan
from ragagent.errors import ApplicationError, error_payload
from ragagent.evaluation.artifacts import usage_delta, usage_snapshot
from ragagent.observability import configure_logging
from ragagent.queues import queue_name


class LocalAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        # FastAPI merges security lists in openapi_extra; mark truly public routes.
        for path in ("/api/health", "/api/ready", "/api/auth/status"):
            schema["paths"][path]["get"]["security"] = []
        return schema


app = LocalAPI(
    title="Scientific RAGAgent",
    version=__version__,
    lifespan=dispatcher_lifespan,
    dependencies=[Depends(HTTPBearer(auto_error=False))],
)
app.include_router(auth.router)
app.include_router(diagnostics.router)
app.include_router(papers.router)
app.include_router(runs.router)
app.include_router(providers.router)
app.include_router(evaluations.router)
app.include_router(conversations.router)
logger = logging.getLogger("ragagent.api")


configure_logging()


@app.middleware("http")
async def trace(request: Request, call_next: Any) -> Any:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    try:
        error = local_request_error(request)
        authentication_error = None if error else auth.auth_error(request)
        if error:
            response = JSONResponse(status_code=403, content=error_payload(error, request_id))
        elif authentication_error:
            response = JSONResponse(
                status_code=401,
                content=error_payload(authentication_error, request_id),
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        else:
            response = await call_next(request)
    except ApplicationError as exc:
        response = JSONResponse(status_code=503, content=error_payload(exc.code, request_id))
    except Exception:
        logger.error(
            "http_failed", extra={"request_id": request_id, "error_code": "internal_error"}
        )
        response = JSONResponse(
            status_code=500, content=error_payload("internal_error", request_id)
        )
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    logger.info(
        "http_request",
        extra={"request_id": request_id, "latency_ms": (time.perf_counter() - start) * 1000},
    )
    return response


@app.exception_handler(ApplicationError)
async def safe_error(request: Request, exc: ApplicationError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_payload(exc.code, getattr(request.state, "request_id", None)),
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic's default response echoes rejected input, which can contain credentials.
    return JSONResponse(
        status_code=422,
        content=error_payload("invalid_request", getattr(request.state, "request_id", None)),
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = exc.detail if isinstance(exc.detail, str) else "invalid_request"
    payload = error_payload(code, getattr(request.state, "request_id", None))
    return JSONResponse(
        status_code=exc.status_code,
        content={**payload, "detail": payload["error_code"]},
        headers=exc.headers,
    )


@app.get("/api/health", openapi_extra={"security": []})
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready", openapi_extra={"security": []})
def ready(db: DB) -> dict[str, str]:
    if auth.expected_verifier() is None:
        raise ApplicationError("local_auth_not_initialized")
    try:
        db.execute(text("SELECT 1"))
        connection = RQQueue().connection()
        connection.ping()
        workers = Worker.all(
            connection=connection, queue=Queue(queue_name("interactive"), connection=connection)
        )
    except Exception:
        raise ApplicationError("infrastructure_unavailable") from None
    if not workers:
        raise ApplicationError("worker_unavailable")
    return {"status": "ready"}


@app.get("/api/queues")
def queues() -> dict[str, dict[str, str | int | bool]]:
    """Report each workload separately; ready only gates interactive availability."""
    try:
        connection = RQQueue().connection()
        result: dict[str, dict[str, str | int | bool]] = {}
        for role in ("interactive", "ingestion", "evaluation"):
            selected = Queue(queue_name(role), connection=connection)
            registered = Worker.all(connection=connection, queue=selected)
            result[role] = {
                "name": selected.name,
                "pending": selected.count,
                "workers": len(registered),
                "available": bool(registered),
            }
        return result
    except Exception:
        raise ApplicationError("infrastructure_unavailable") from None


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
                **error_payload(
                    exc.code if isinstance(exc, ApplicationError) else "internal_error"
                ),
                "usage": {"embedding": usage_delta(before, usage_snapshot(embedder))},
                "usage_scope": "current_request",
            },
        )
    return SearchResponse(
        **result.model_dump(),
        usage={"embedding": usage_delta(before, usage_snapshot(embedder))},
    )
