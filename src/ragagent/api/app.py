import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ragagent.api import evaluations, papers, providers, runs
from ragagent.api.dependencies import get_search
from ragagent.api.papers import DB
from ragagent.api.schemas import QueryRequest
from ragagent.domain.research import QueryPlan, SearchResult
from ragagent.errors import ApplicationError
from ragagent.observability import configure_logging

app = FastAPI(title="Scientific RAGAgent", version="0.1.0")
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
        response = await call_next(request)
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


@app.post("/api/search")
async def search(request: QueryRequest, db: DB) -> SearchResult:
    result = await get_search(db).search(
        QueryPlan(queries=[request.query], filters=request.filters)
    )
    db.commit()
    return result
