"""Authenticated infrastructure/configuration diagnostics; never performs inference."""

from fastapi import APIRouter
from sqlalchemy import text

from ragagent.api import auth
from ragagent.api.papers import DB
from ragagent.api.queue import RQQueue
from ragagent.build_info import build_info
from ragagent.errors import ApplicationError
from ragagent.providers.config import load_config
from ragagent.providers.environment import runtime_value
from ragagent.queues import queue_name
from ragagent.settings import get_settings

router = APIRouter(tags=["diagnostics"])


@router.get("/api/diagnostics")
def diagnostics(db: DB) -> dict[str, object]:
    from rq import Queue, Worker

    result: dict[str, object] = {
        "build": build_info(),
        "local_auth": "initialized" if auth.expected_verifier() else "not_initialized",
        "inference": "not_tested",
    }
    try:
        db.execute(text("SELECT 1"))
        result["database"] = "available"
    except Exception:
        db.rollback()
        result["database"] = "unavailable"
    try:
        connection = RQQueue().connection()
        connection.ping()
        result["redis"] = "available"
        result["queues"] = {
            role: {
                "pending": selected.count,
                "workers": len(Worker.all(connection=connection, queue=selected)),
            }
            for role in ("interactive", "ingestion", "evaluation")
            for selected in [Queue(queue_name(role), connection=connection)]
        }
    except Exception:
        result["redis"] = "unavailable"
        result["queues"] = None
    settings = get_settings()
    try:
        config = load_config(settings.agent_config)
        result["chat_configuration"] = {
            role: {
                "provider": model.provider,
                "credential_configured": bool(runtime_value(model.key_environment))
                if model.key_environment
                else True,
                "connectivity": "not_tested",
            }
            for role in ("supervisor", "retriever", "analyst", "reviewer")
            for model in [getattr(config.agents, role)]
        }
    except ApplicationError as exc:
        result["chat_configuration"] = {"error_code": exc.code}
    result["retrieval_configuration"] = {
        "embedding_backend": settings.embedding_backend,
        "embedding_dimension": settings.embedding_dimension,
        "reranker_backend": settings.reranker_backend,
        "model_loading": "not_tested",
        "index_compatibility": "not_tested",
    }
    return result
