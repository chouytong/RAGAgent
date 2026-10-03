import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ragagent.api import papers, providers, runs
from ragagent.api.dependencies import get_search
from ragagent.api.papers import DB
from ragagent.api.schemas import QueryRequest
from ragagent.domain.research import QueryPlan, SearchResult
from ragagent.errors import ApplicationError

app = FastAPI(title="Scientific RAGAgent", version="0.1.0")
app.include_router(papers.router)
app.include_router(runs.router)
app.include_router(providers.router)
logger = logging.getLogger("ragagent.api")


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "event": record.getMessage(),
                "level": record.levelname,
                "request_id": getattr(record, "request_id", None),
                "trace_id": getattr(record, "trace_id", None),
                "error_code": getattr(record, "error_code", None),
            }
        )


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.getLogger("ragagent").addHandler(handler)
logging.getLogger("ragagent").setLevel(logging.INFO)


@app.middleware("http")
async def trace(request: Request, call_next: Any) -> Any:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
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
