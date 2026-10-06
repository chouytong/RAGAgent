from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ragagent.api.papers import DB, QueueDep, enqueue
from ragagent.api.schemas import RunResponse
from ragagent.db.models import Run
from ragagent.evaluation.conversation_schema import (
    ConversationEvaluationRequest,
    validate_conversation_references,
)
from ragagent.evaluation.schema import EvaluationRequest
from ragagent.evaluation.validation import validate_references
from ragagent.settings import get_settings

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def submit(kind: str, request: EvaluationRequest, db: DB, queue: QueueDep) -> RunResponse:
    try:
        if kind == "eval_retrieval":
            request.dataset.runnable()
        else:
            request.dataset.generation_runnable()
        validate_references(request.dataset, db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if request.resume_run_id:
        previous = db.get(Run, request.resume_run_id)
        if not previous or previous.kind != kind or previous.status not in {"completed", "failed"}:
            raise HTTPException(422, "evaluation_resume_run_unavailable")
        if previous.request.get("dataset") != request.dataset.model_dump(mode="json"):
            raise HTTPException(422, "evaluation_resume_dataset_mismatch")
        path = get_settings().data_dir / "evaluations" / previous.id / "results.json"
        if not path.is_file():
            raise HTTPException(422, "evaluation_resume_artifact_unavailable")
    return RunResponse.model_validate(enqueue(db, queue, kind, request.model_dump()))


@router.post("/retrieval", status_code=202)
def retrieval(request: EvaluationRequest, db: DB, queue: QueueDep) -> RunResponse:
    return submit("eval_retrieval", request, db, queue)


@router.post("/rag", status_code=202)
def rag(request: EvaluationRequest, db: DB, queue: QueueDep) -> RunResponse:
    return submit("eval_rag", request, db, queue)


@router.post("/multi-agent", status_code=202)
def research(request: EvaluationRequest, db: DB, queue: QueueDep) -> RunResponse:
    return submit("eval_multi_agent", request, db, queue)


@router.post("/conversation", status_code=202)
def conversation(request: ConversationEvaluationRequest, db: DB, queue: QueueDep) -> RunResponse:
    try:
        validate_conversation_references(request.dataset, db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if request.resume_run_id:
        previous = db.get(Run, request.resume_run_id)
        if (
            previous is None
            or previous.kind != "eval_conversation"
            or previous.status not in {"completed", "failed"}
        ):
            raise HTTPException(422, "evaluation_resume_run_unavailable")
        if previous.request.get("dataset") != request.dataset.model_dump(mode="json"):
            raise HTTPException(422, "evaluation_resume_dataset_mismatch")
        if not (get_settings().data_dir / "evaluations" / previous.id / "results.json").is_file():
            raise HTTPException(422, "evaluation_resume_artifact_unavailable")
    return RunResponse.model_validate(enqueue(db, queue, "eval_conversation", request.model_dump()))


@router.get("/{run_id}/{filename}")
def artifact(run_id: str, filename: str, db: DB) -> FileResponse:
    if filename not in {"results.json", "results.md"}:
        raise HTTPException(404, "artifact_not_found")
    run = db.get(Run, run_id)
    if not run or not run.kind.startswith("eval_") or run.status not in {"completed", "failed"}:
        raise HTTPException(404, "evaluation_not_terminal")
    path = get_settings().data_dir / "evaluations" / run.id / filename
    if not path.exists():
        raise HTTPException(404, "artifact_not_found")
    return FileResponse(path, filename=filename)
