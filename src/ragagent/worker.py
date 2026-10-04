import asyncio
import hashlib
import logging
from typing import Any, TypeVar

from fastapi.encoders import jsonable_encoder
from langchain_core.runnables import Runnable
from pydantic import BaseModel
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ragagent.db.models import Author, ExecutionEvent, Paper, PaperAuthor, Run, new_id
from ragagent.db.session import session_factory
from ragagent.errors import ApplicationError
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import MultiAgentState, RAGState
from ragagent.ingestion.arxiv import arxiv_identity, download_arxiv
from ragagent.ingestion.chunker import StructureChunker
from ragagent.ingestion.parser import DoclingParser
from ragagent.ingestion.service import ingest
from ragagent.jobs import claim_run, ensure_running, fail_run, finish_run
from ragagent.observability import configure_logging
from ragagent.providers.chat import Usage, usage_record
from ragagent.retrieval.service import HybridRetriever
from ragagent.runtime import make_agents, make_embedder, make_reranker
from ragagent.settings import get_settings

T = TypeVar("T", bound=BaseModel)
configure_logging()
logger = logging.getLogger("ragagent.worker")


def persist_usage(session: Session, run: Run, tracked: dict[str, Usage]) -> None:
    """Checkpoint charges without committing the caller's indexing transaction."""
    with Session(session.get_bind(), expire_on_commit=False) as checkpoint:
        current = checkpoint.get(Run, run.id)
        if current is None:
            raise ApplicationError("run_no_longer_active")
        ensure_running(checkpoint, current)
        current.result = {
            **(current.result or {}),
            "usage": {name: usage_record(usage) for name, usage in tracked.items()},
            "usage_scope": "current_attempt",
        }
        checkpoint.commit()


def track_usage(
    session: Session, run: Run, tracked: dict[str, Usage], name: str, adapter: Any
) -> None:
    usage = getattr(adapter, "usage", None)
    if isinstance(usage, Usage):
        # Local model adapters retain weights across jobs, but accounting is per Run.
        usage = adapter.usage = Usage()
        tracked[name] = usage
        usage.on_update = lambda _: persist_usage(session, run, tracked)


def event(session: Session, run: Run, node: str, payload: dict[str, Any]) -> None:
    ensure_running(session, run)
    session.add(ExecutionEvent(run_id=run.id, node=node, payload=payload))
    session.commit()


async def graph_events(
    graph: Runnable[Any, Any], state: T, session: Session, run: Run, recursion_limit: int
) -> T:
    async for updates in graph.astream(
        state, {"recursion_limit": recursion_limit}, stream_mode="updates"
    ):
        for node, update in updates.items():
            data = jsonable_encoder(update)
            state = type(state).model_validate({**state.model_dump(), **data})
            event(session, run, node, data)
    return state


async def arxiv_ingestion(session: Session, run: Run) -> Paper:
    settings = get_settings()
    arxiv_id = run.request["arxiv_id"]
    _, requested_version = arxiv_identity(arxiv_id)
    # A pinned source is immutable. Unversioned imports must resolve Atom again
    # to discover new versions rather than permanently returning an older PDF.
    if requested_version is not None:
        existing = session.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
        if existing:
            return existing
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / f"{new_id()}.pdf"
    try:
        metadata = await download_arxiv(arxiv_id, path, settings.max_upload_bytes)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        ensure_running(session, run)
        # Let the database arbitrate concurrent imports of the same version.
        # Hash equality alone never assigns a new version to an older source.
        paper_id = session.scalar(
            insert(Paper)
            .values(
                title=metadata.title,
                arxiv_id=metadata.arxiv_id,
                arxiv_family_id=metadata.arxiv_family_id,
                arxiv_version=metadata.arxiv_version,
                source_status="unknown",
                year=metadata.year,
                source_url=metadata.source_url,
                sha256=sha,
                original_path=str(path),
                status="queued",
            )
            .on_conflict_do_nothing()
            .returning(Paper.id)
        )
        if paper_id is None:
            existing = session.scalar(select(Paper).where(Paper.arxiv_id == metadata.arxiv_id))
            if existing is None:
                raise ApplicationError("paper_changed_retry")
            path.unlink(missing_ok=True)
            return existing
        names = list(dict.fromkeys(n.strip() for n in metadata.authors if n.strip()))
        author_ids = {}
        for name in sorted(names):
            author_id = session.scalar(
                insert(Author)
                .values(name=name)
                .on_conflict_do_nothing(index_elements=[Author.name])
                .returning(Author.id)
            )
            if author_id is None:
                author_id = session.scalar(select(Author.id).where(Author.name == name))
            if author_id is None:
                raise ApplicationError("author_changed_retry")
            author_ids[name] = author_id
        for position, name in enumerate(names):
            session.add(
                PaperAuthor(paper_id=paper_id, author_id=author_ids[name], position=position)
            )
        session.commit()
        paper = session.get(Paper, paper_id)
        assert paper is not None
        return paper
    except Exception:
        session.rollback()
        # A failure after commit must not delete a successfully registered PDF.
        if not session.scalar(select(Paper.id).where(Paper.original_path == str(path))):
            path.unlink(missing_ok=True)
        raise


async def execute_async(run_id: str) -> None:
    settings = get_settings()
    with session_factory()() as session:
        run = claim_run(session, run_id)
        if run is None:
            return
        paper: Paper | None = None
        tracked: dict[str, Usage] = {}
        try:
            if run.kind in {"ingestion", "arxiv"}:
                paper = (
                    await arxiv_ingestion(session, run)
                    if run.kind == "arxiv"
                    else session.get(Paper, run.request["paper_id"])
                )
                if paper is None:
                    raise ValueError("paper_not_found")
                ensure_running(session, run)
                paper = session.scalar(
                    select(Paper)
                    .where(Paper.id == paper.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert paper is not None
                if paper.embedding_model is None:
                    if paper.status not in {"queued", "failed"}:
                        raise ApplicationError("paper_already_processing")
                    paper.status = "parsing"
                    event(session, run, "parsing", {"paper_id": paper.id})

                def ingestion_progress(status: str) -> None:
                    assert paper is not None
                    paper.status = status
                    event(session, run, status, {"paper_id": paper.id})

                if paper.embedding_model is None:
                    embedder = make_embedder(settings)
                    track_usage(session, run, tracked, "embedding", embedder)
                    await ingest(
                        session,
                        paper,
                        DoclingParser(),
                        StructureChunker(
                            settings.chunk_target_tokens, settings.chunk_overlap_tokens
                        ),
                        embedder,
                        ingestion_progress,
                    )
                paper.status = "indexed"
                run.result = {"paper_id": paper.id}
                run.status = "completed"
            elif run.kind in {"rag", "research"}:
                agents = make_agents(settings)
                for name, adapter in agents.items():
                    track_usage(session, run, tracked, name, adapter)
                embedder = make_embedder(settings)
                track_usage(session, run, tracked, "embedding", embedder)
                search = HybridRetriever(
                    session,
                    embedder,
                    make_reranker(settings),
                    settings.candidate_top_n,
                    settings.evidence_top_k,
                    settings.rrf_k,
                )
                if run.kind == "rag":
                    graph = build_rag(
                        search,
                        agents["supervisor"],
                        agents["retriever"],
                        agents["analyst"],
                        agents["reviewer"],
                        settings.max_retrieval_retries,
                        settings.minimum_rerank_score,
                        max_evidence_records=settings.rag_evidence_budget,
                    )
                    state = await graph_events(
                        graph, RAGState(**run.request, trace_id=run.trace_id), session, run, 100
                    )
                    run.result, run.status = state.model_dump(mode="json"), state.status
                else:
                    research_graph = build_research(
                        search,
                        agents["supervisor"],
                        agents["retriever"],
                        agents["analyst"],
                        agents["reviewer"],
                        settings.max_retrieval_retries + 1,
                        settings.max_revisions,
                        settings.max_iterations,
                        min_rerank_score=settings.minimum_rerank_score,
                        max_evidence_records=settings.research_evidence_budget,
                    )
                    research = await graph_events(
                        research_graph,
                        MultiAgentState(
                            **run.request,
                            trace_id=run.trace_id,
                            trace_metadata={"run_id": run.id, "workflow": "research-v1"},
                        ),
                        session,
                        run,
                        300,
                    )
                    run.result, run.status = research.model_dump(mode="json"), research.status
                run.result["usage"] = {name: usage_record(usage) for name, usage in tracked.items()}
                run.result["usage_scope"] = "current_attempt"
            elif run.kind.startswith("eval_"):
                from ragagent.evaluation.generation import evaluate_generation
                from ragagent.evaluation.retrieval import evaluate_retrieval
                from ragagent.evaluation.schema import EvaluationDataset
                from ragagent.evaluation.validation import validate_references
                from ragagent.providers.chat import LiteLLMProvider
                from ragagent.providers.config import load_config

                dataset = EvaluationDataset.model_validate(run.request["dataset"])
                validate_references(dataset, session)
                embedder = make_embedder(settings)
                track_usage(session, run, tracked, "embedding", embedder)
                search = HybridRetriever(
                    session,
                    embedder,
                    make_reranker(settings),
                    settings.candidate_top_n,
                    settings.evidence_top_k,
                    settings.rrf_k,
                )
                directory = settings.data_dir / "evaluations" / run.id
                resume_id = run.request.get("resume_run_id")
                resume_directory = None
                resume_usage = None
                if resume_id:
                    previous = session.get(Run, resume_id)
                    if previous is None or previous.kind != run.kind:
                        raise ApplicationError("evaluation_resume_run_unavailable")
                    resume_directory = settings.data_dir / "evaluations" / previous.id
                    resume_usage = (previous.result or {}).get("usage")
                if run.kind == "eval_retrieval":
                    run.result = await evaluate_retrieval(
                        dataset,
                        search,
                        session,
                        settings,
                        directory,
                        resume_directory=resume_directory,
                        resume_usage=resume_usage,
                    )
                elif run.kind in {"eval_rag", "eval_multi_agent"}:
                    agents = make_agents(settings)
                    for name, adapter in agents.items():
                        track_usage(session, run, tracked, name, adapter)
                    judge = LiteLLMProvider(
                        load_config(settings.agent_config).agents.reviewer,
                        settings.provider_timeout,
                    )
                    track_usage(session, run, tracked, "judge", judge)
                    run.result = await evaluate_generation(
                        dataset,
                        search,
                        agents,
                        judge,
                        settings,
                        directory,
                        multi_agent=run.kind == "eval_multi_agent",
                        resume_directory=resume_directory,
                        resume_usage=resume_usage,
                    )
                else:
                    raise ValueError("unknown_evaluation_kind")
                run.status = "completed" if run.result.get("status") == "completed" else "failed"
                if run.status == "failed":
                    run.error_code = run.result.get("error_code") or "evaluation_cases_failed"
            else:
                raise ValueError("unknown_job_kind")
            if run.kind in {"ingestion", "arxiv"} and tracked:
                run.result = {
                    **(run.result or {}),
                    "usage": {name: usage_record(usage) for name, usage in tracked.items()},
                    "usage_scope": "current_attempt",
                }
            finish_run(session, run)
        except Exception as exc:
            session.rollback()
            code = exc.code if isinstance(exc, ApplicationError) else "job_failed"
            if tracked:
                try:
                    persist_usage(session, run, tracked)
                except ApplicationError as usage_exc:
                    if usage_exc.code != "run_no_longer_active":
                        raise
            fail_run(session, run_id, code)
            logger.error("job_failed", extra={"trace_id": run.trace_id, "error_code": code})
            # Re-raise only a safe error so RQ persistence/logging cannot leak exception messages.
            raise ApplicationError(code) from None
        finally:
            for usage in tracked.values():
                usage.on_update = None


def execute(run_id: str) -> None:
    asyncio.run(execute_async(run_id))


def main() -> None:
    connection = Redis.from_url(get_settings().redis_url.get_secret_value())
    Worker(
        [Queue("research", connection=connection)],
        connection=connection,
        work_horse_killed_handler=on_work_horse_killed,
    ).work()


def on_failure(job: Any, connection: Any, exc_type: Any, exc_value: Any, traceback: Any) -> None:
    """Persist process interruption without storing raw exception text or prompts."""
    try:
        with session_factory()() as session:
            run = session.get(Run, job.id)
            trace_id = run.trace_id if run is not None else None
            fail_run(session, job.id, "worker_interrupted")
            logger.error(
                "worker_interrupted",
                extra={"trace_id": trace_id, "error_code": "worker_interrupted"},
            )
    except Exception:
        raise ApplicationError("failure_recording_failed") from None


def on_stopped(job: Any, connection: Any) -> None:
    on_failure(job, connection, None, None, None)


def on_work_horse_killed(job: Any, retpid: Any, ret_val: Any, rusage: Any) -> None:
    # RQ's parent does not invoke the ordinary failure callback for a killed
    # child. This handler runs in the surviving parent and persists the failure.
    on_failure(job, job.connection, None, None, None)


if __name__ == "__main__":
    main()
