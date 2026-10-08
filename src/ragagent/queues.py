"""Shared routing contract for durable dispatch, readiness and dedicated workers."""

import re
from typing import Literal

from ragagent.db.models import Run
from ragagent.errors import ApplicationError
from ragagent.settings import get_settings

QueueRole = Literal["interactive", "ingestion", "evaluation", "legacy"]


def queue_name(role: QueueRole) -> str:
    settings = get_settings()
    return {
        "interactive": settings.interactive_queue,
        "ingestion": settings.ingestion_queue,
        "evaluation": settings.evaluation_queue,
        "legacy": "research",
    }[role]


def freeze_queue(run: Run) -> str:
    frozen = run.request.get("_queue_name")
    if isinstance(frozen, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", frozen):
        return frozen
    if run.kind in {"rag", "research"}:
        role: QueueRole = "interactive"
    elif run.kind in {"ingestion", "arxiv"}:
        role = "ingestion"
    elif run.kind.startswith("eval_"):
        role = "evaluation"
    else:
        raise ApplicationError("unknown_job_kind")
    selected = queue_name(role)
    run.request = {**run.request, "_queue_name": selected}
    return selected
