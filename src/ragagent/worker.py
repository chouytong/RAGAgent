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
from sqlalchemy.orm import Session

from ragagent.db.models import Author, ExecutionEvent, Paper, PaperAuthor, Run, new_id
from ragagent.db.session import session_factory
from ragagent.errors import ApplicationError
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import MultiAgentState, RAGState
from ragagent.ingestion.arxiv import download_arxiv
from ragagent.ingestion.chunker import StructureChunker
from ragagent.ingestion.parser import DoclingParser
from ragagent.ingestion.service import ingest
from ragagent.retrieval.service import HybridRetriever
from ragagent.runtime import make_agents, make_embedder, make_reranker
from ragagent.settings import get_settings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("ragagent.worker")


def event(session: Session, run: Run, node: str, payload: dict[str, Any]) -> None:
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
    existing = session.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
    if existing:
        return existing
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / f"{new_id()}.pdf"
    try:
        metadata = await download_arxiv(arxiv_id, path, settings.max_upload_bytes)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = session.scalar(select(Paper).where(Paper.sha256 == sha))
        if existing:
            path.unlink(missing_ok=True)
            return existing
        paper = Paper(
            title=metadata.title,
            arxiv_id=metadata.arxiv_id,
            year=metadata.year,
            source_url=metadata.source_url,
            sha256=sha,
            original_path=str(path),
            status="parsing",
        )
        session.add(paper)
        session.flush()
        for position, name in enumerate(metadata.authors):
            author = session.scalar(select(Author).where(Author.name == name))
            if author is None:
                author = Author(name=name)
                session.add(author)
                session.flush()
            session.add(PaperAuthor(paper_id=paper.id, author_id=author.id, position=position))
        session.commit()
        return paper
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise


async def execute_async(run_id: str) -> None:
    settings = get_settings()
    with session_factory()() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError("run_not_found")
        if run.status in {"completed", "insufficient_evidence"}:
            return
        run.status = "running"
        event(session, run, "started", {"trace_id": run.trace_id, "kind": run.kind})
        paper: Paper | None = None
        try:
            if run.kind in {"ingestion", "arxiv"}:
                paper = (
                    await arxiv_ingestion(session, run)
                    if run.kind == "arxiv"
                    else session.get(Paper, run.request["paper_id"])
                )
                if paper is None:
                    raise ValueError("paper_not_found")
                paper.status = "parsing"
                event(session, run, "parsing", {"paper_id": paper.id})
                if paper.embedding_model is None:
                    await ingest(
                        session,
                        paper,
                        DoclingParser(),
                        StructureChunker(
                            settings.chunk_target_tokens, settings.chunk_overlap_tokens
                        ),
                        make_embedder(settings),
                    )
                paper.status = "indexed"
                run.result = {"paper_id": paper.id}
                run.status = "completed"
                session.commit()
            elif run.kind in {"rag", "research"}:
                agents = make_agents(settings)
                search = HybridRetriever(
                    session,
                    make_embedder(settings),
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
                    )
                    research = await graph_events(
                        research_graph,
                        MultiAgentState(**run.request, trace_id=run.trace_id),
                        session,
                        run,
                        300,
                    )
                    run.result, run.status = research.model_dump(mode="json"), research.status
                run.result["usage"] = {
                    name: {
                        "prompt_tokens": p.usage.prompt_tokens,
                        "completion_tokens": p.usage.completion_tokens,
                        "cost": p.usage.cost,
                    }
                    for name, p in agents.items()
                }
                session.commit()
            else:
                raise ValueError("unknown_job_kind")
            event(session, run, "finished", {"status": run.status})
        except Exception as exc:
            session.rollback()
            code = exc.code if isinstance(exc, ApplicationError) else "job_failed"
            run.status, run.error_code = "failed", code
            if paper is not None:
                paper.status, paper.error_code = "failed", code
            event(session, run, "failed", {"error_code": code, "trace_id": run.trace_id})
            logger.error("job_failed", extra={"trace_id": run.trace_id, "error_code": code})
            # Re-raise only a safe error so RQ persistence/logging cannot leak exception messages.
            raise ApplicationError(code) from None


def execute(run_id: str) -> None:
    asyncio.run(execute_async(run_id))


def main() -> None:
    connection = Redis.from_url(get_settings().redis_url.get_secret_value())
    Worker([Queue("research", connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
