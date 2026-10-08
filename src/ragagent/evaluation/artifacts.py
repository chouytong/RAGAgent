import hashlib
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Text, cast, select
from sqlalchemy.orm import Session

from ragagent.build_info import build_info
from ragagent.db.models import Author, Chunk, ChunkEntity, Entity, Paper, PaperAuthor, Section
from ragagent.errors import EvaluationError
from ragagent.evaluation.schema import EvaluationDataset
from ragagent.providers.chat import ChatProvider, Usage, usage_record
from ragagent.providers.config import AgentModel
from ragagent.providers.embedding import normalize_endpoint
from ragagent.settings import Settings


def source_commit() -> str:
    # Missing Git is an explicit unknown identity, never an evaluation crash.
    return str(build_info()["source_commit"])


def source_snapshot() -> dict[str, Any]:
    """Identify the source actually present, including uncommitted edits, without secrets."""
    root = Path(__file__).resolve().parents[3]
    if not (root / "src" / "ragagent").is_dir():
        root = Path.cwd()
    files = sorted(
        [
            path
            for directory in (root / "src", root / "migrations")
            for path in directory.rglob("*")
            if path.is_file() and path.suffix in {".py", ".mako"}
        ]
        + [path for path in (root / "pyproject.toml", root / "uv.lock") if path.is_file()]
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    try:
        dirty: bool | None = bool(
            subprocess.check_output(
                [
                    "git",
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--",
                    "src",
                    "migrations",
                    "pyproject.toml",
                    "uv.lock",
                ],
                cwd=root,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        dirty = None
    return {
        "source_hash": digest.hexdigest() if files else None,
        "source_dirty": dirty,
        "source_file_count": len(files),
        "source_hash_scope": "src/**/*.py; migrations/**/*.{py,mako}; pyproject.toml; uv.lock",
    }


def canonical_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def usage_snapshot(provider: Any) -> dict[str, Any]:
    usage = getattr(provider, "usage", None)
    if isinstance(usage, Usage):
        return {**usage_record(usage), "accounting_available": True}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "cost": getattr(usage, "cost", None),
        "known_cost": getattr(usage, "known_cost", 0.0),
        "unknown_cost_calls": getattr(usage, "unknown_cost_calls", 0),
        "calls": getattr(usage, "calls", 0),
        "in_flight_calls": getattr(usage, "in_flight_calls", 0),
        "accounting_available": hasattr(usage, "unknown_cost_calls"),
        "provider_models": list(getattr(usage, "provider_models", [])),
        "system_fingerprints": list(getattr(usage, "system_fingerprints", [])),
    }


def usage_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: current[key] - previous[key]
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "known_cost",
            "unknown_cost_calls",
            "calls",
            "in_flight_calls",
        )
    }
    result["accounting_available"] = current["accounting_available"]
    for key in ("provider_models", "system_fingerprints"):
        result[key] = list(dict.fromkeys([*previous.get(key, []), *current.get(key, [])]))
    if current["accounting_available"]:
        result["cost"] = (
            None
            if result["unknown_cost_calls"] or result["in_flight_calls"]
            else result["known_cost"]
        )
    else:
        result["cost"] = (
            current["cost"] - previous["cost"]
            if current["cost"] is not None and previous["cost"] is not None
            else None
        )
    return result


@contextmanager
def checkpoint_usage(
    providers: Mapping[str, Any], checkpoint: Callable[[], None]
) -> Iterator[None]:
    """Checkpoint before dispatch and after accounting, preserving the worker observer."""
    observers: dict[int, tuple[Usage, Callable[[Usage], None] | None]] = {}
    try:
        for provider in providers.values():
            usage = getattr(provider, "usage", None)
            if not isinstance(usage, Usage) or id(usage) in observers:
                continue
            previous = usage.on_update
            observers[id(usage)] = (usage, previous)

            def observe(
                updated: Usage, original: Callable[[Usage], None] | None = previous
            ) -> None:
                try:
                    if original is not None:
                        original(updated)
                finally:
                    checkpoint()

            usage.on_update = observe
        yield
    finally:
        for usage, original in observers.values():
            usage.on_update = original


def workflow_usage(usage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Sum chat and retrieval embedding costs, excluding the separate judge."""
    workflow = [value for name, value in usage.items() if name != "judge"]
    return {
        "total_workflow_tokens": sum(
            value["prompt_tokens"] + value["completion_tokens"] for value in workflow
        ),
        "total_workflow_cost": sum(value["cost"] for value in workflow)
        if all(value["cost"] is not None for value in workflow)
        else None,
        "known_workflow_cost": sum(value["known_cost"] for value in workflow),
        "unknown_workflow_cost_calls": sum(value["unknown_cost_calls"] for value in workflow),
        "workflow_accounting_available": all(value["accounting_available"] for value in workflow),
    }


def resume_results(
    directory: Path | None, kind: str, provenance: dict[str, Any]
) -> dict[str, Any] | None:
    if directory is None:
        return None
    try:
        previous = json.loads((directory / "results.json").read_text())
    except (OSError, ValueError):
        raise EvaluationError("evaluation_resume_artifact_unavailable") from None
    if not isinstance(previous, dict) or previous.get("kind") != kind:
        raise EvaluationError("evaluation_resume_kind_mismatch")
    prior = previous.get("manifest", {})
    identity = (
        "dataset_hash",
        "source_hash",
        "model_configuration",
        "workflow_configuration",
        "retrieval_configuration",
        "corpus_snapshot",
        "judge",
    )
    if (
        previous.get("corpus_verification") == "failed"
        or provenance["corpus_snapshot"].get("availability") != "available"
        or any(prior.get(key) != provenance.get(key) for key in identity)
    ):
        raise EvaluationError("evaluation_resume_identity_mismatch")
    provenance["resume_from"] = {
        "run_id": directory.name,
        "manifest_hash": canonical_hash(prior),
    }
    return previous


def previous_attempts(
    previous: dict[str, Any] | None,
    persisted_usage: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if previous is None:
        return []
    usage = {name: dict(value) for name, value in previous.get("usage", {}).items()}
    if persisted_usage is not None:
        for name, snapshot in persisted_usage.items():
            usage[name] = {**usage.get(name, {}), **snapshot}
            usage[name].setdefault("accounting_available", "unknown_cost_calls" in snapshot)
    return [
        *previous.get("previous_attempt_usage", []),
        {
            "timestamp": previous["manifest"]["timestamp"],
            "usage": usage,
            "usage_source": "persisted_run" if persisted_usage is not None else "artifact",
            "status": previous.get("status", "unknown"),
        },
    ]


def evaluation_status(successful: int, failed: int, pending: int) -> str:
    if pending:
        return "running"
    if failed:
        return "partial" if successful else "failed"
    return "completed"


def provider_snapshot(provider: ChatProvider) -> dict[str, Any]:
    """Record the instantiated adapter, never a configuration file reread after execution."""
    model = getattr(provider, "model", None)
    return {
        "adapter": type(provider).__name__,
        "request_timeout_seconds": getattr(provider, "timeout", None),
        "configuration_available": isinstance(model, AgentModel),
        **(model.model_dump(mode="json") if isinstance(model, AgentModel) else {}),
    }


def retrieval_snapshot(search: Any, settings: Settings) -> dict[str, Any]:
    dense = getattr(search, "dense", None)
    embedder = getattr(dense, "embedder", None)
    reranker = getattr(search, "reranker", None)
    fusion = getattr(search, "fusion", None)
    return {
        "candidate_top_n": getattr(search, "top_n", None),
        "evidence_top_k": getattr(search, "top_k", None),
        "rrf_k": getattr(fusion, "k", None),
        "minimum_rerank_score": settings.minimum_rerank_score,
        "embedder_adapter": type(embedder).__name__ if embedder is not None else None,
        "embedding_fingerprint": getattr(embedder, "fingerprint", None),
        "embedding_model": getattr(embedder, "model_name", getattr(embedder, "model", None)),
        "embedding_revision": getattr(embedder, "revision", None),
        "embedding_api_base": normalize_endpoint(getattr(embedder, "api_base", None)),
        "embedding_dimension": getattr(embedder, "dimension", None),
        "reranker_adapter": type(reranker).__name__ if reranker is not None else None,
        "reranker_model": getattr(reranker, "model_name", None),
        "reranker_revision": getattr(reranker, "revision", None),
        "configuration_available": dense is not None and reranker is not None,
    }


def corpus_snapshot(session: Session | None) -> dict[str, Any]:
    """Hash all inputs affecting indexed retrieval, including metadata, vectors and filters."""
    if session is None:
        return {"availability": "unavailable", "reason": "search_has_no_corpus_session"}
    indexed = Paper.status == "indexed"
    statements = {
        "papers": select(
            Paper.id,
            Paper.title,
            Paper.year,
            Paper.venue,
            Paper.arxiv_id,
            Paper.arxiv_family_id,
            Paper.arxiv_version,
            Paper.source_status,
            Paper.source_url,
            Paper.sha256,
            Paper.embedding_model,
        )
        .where(indexed)
        .order_by(Paper.id),
        "authors": select(
            PaperAuthor.paper_id,
            Author.id,
            Author.name,
            PaperAuthor.position,
        )
        .join(Author)
        .join(Paper)
        .where(indexed)
        .order_by(PaperAuthor.paper_id, Author.id),
        "sections": select(
            Section.id,
            Section.paper_id,
            Section.parent_id,
            Section.title,
            Section.path,
            Section.identity,
            Section.ordinal,
        )
        .join(Paper)
        .where(indexed)
        .order_by(Section.id),
        "chunks": select(
            Chunk.id,
            Chunk.paper_id,
            Chunk.section_id,
            Chunk.section_path,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.element_type,
            Chunk.content,
            Chunk.token_count,
            Chunk.ordinal,
            Chunk.metadata_json,
            cast(Chunk.embedding, Text).label("embedding"),
        )
        .join(Paper)
        .where(indexed)
        .order_by(Chunk.id),
        "entities": select(
            ChunkEntity.chunk_id,
            Entity.id,
            Entity.name,
            Entity.entity_type,
        )
        .join(Entity)
        .join(Chunk)
        .join(Paper)
        .where(indexed)
        .order_by(
            ChunkEntity.chunk_id,
            Entity.id,
        ),
    }
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    for name, statement in statements.items():
        digest.update(name.encode() + b"\n")
        counts[name] = 0
        for row in session.execute(statement).mappings():
            digest.update(canonical_hash(dict(row)).encode() + b"\n")
            counts[name] += 1
    return {
        "availability": "available",
        "hash": digest.hexdigest(),
        "algorithm": "sha256",
        "scope": "indexed_papers_authors_sections_chunks_vectors_entities_v2",
        "counts": counts,
    }


def verify_corpus_snapshot(session: Session | None, initial: dict[str, Any]) -> None:
    if initial != corpus_snapshot(session):
        raise EvaluationError("corpus_changed_during_evaluation")


def manifest(
    dataset: EvaluationDataset,
    settings: Settings,
    *,
    model_configuration: dict[str, Any] | None = None,
    retrieval_configuration: dict[str, Any] | None = None,
    corpus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "git_commit": source_commit(),
        "build": build_info(),
        **source_snapshot(),
        "dataset_hash": canonical_hash(dataset.model_dump(mode="json")),
        "dataset_id": dataset.dataset_id,
        "label_source": dataset.label_source,
        "warning": dataset.warning,
        "timestamp": datetime.now(UTC).isoformat(),
        "model_configuration": model_configuration or {},
        "workflow_configuration": {
            "max_retrieval_retries": settings.max_retrieval_retries,
            "max_revisions": settings.max_revisions,
            "max_iterations": settings.max_iterations,
            "rag_evidence_budget": settings.rag_evidence_budget,
            "research_evidence_budget": settings.research_evidence_budget,
        },
        "corpus_snapshot": corpus or {"availability": "unavailable"},
        "retrieval_configuration": retrieval_configuration
        if retrieval_configuration is not None
        else {
            "configuration_available": False,
            "candidate_top_n": settings.candidate_top_n,
            "evidence_top_k": settings.evidence_top_k,
            "rrf_k": settings.rrf_k,
            "minimum_rerank_score": settings.minimum_rerank_score,
            "embedding_backend": settings.embedding_backend,
            "embedding_model": settings.embedding_model,
            "embedding_revision": settings.embedding_revision,
            "embedding_api_base": normalize_endpoint(settings.embedding_api_base),
            "embedding_dimension": settings.embedding_dimension,
            "reranker_model": settings.reranker_model,
            "reranker_revision": settings.reranker_revision,
        },
    }


def write_results(directory: Path, result: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "results.json.tmp"
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(directory / "results.json")
    corpus_hash = result["manifest"].get("corpus_snapshot", {}).get("hash", "unavailable")
    lines = [
        "# Evaluation results",
        "",
        result["manifest"]["warning"],
        "",
        f"Commit: `{result['manifest']['git_commit']}`",
        f"Source hash: `{result['manifest'].get('source_hash', 'unavailable')}`",
        f"Dataset hash: `{result['manifest']['dataset_hash']}`",
        f"Corpus hash: `{corpus_hash}`",
        "",
        "Metrics are calculated from this run. Null means unavailable/undefined.",
        f"Evaluation status: {result.get('status', 'unknown')}",
        f"Failed cases: {result.get('failed_cases', 0)}; "
        f"pending cases: {result.get('pending_cases', 0)}.",
        "Summary includes successfully evaluated cases only; see summary_case_count.",
        "Usage and total costs cover the current attempt, including failures. "
        "Previous attempt costs are retained separately in previous_attempt_usage.",
        "",
        "```json",
        json.dumps(result.get("summary", {}), indent=2),
        "```",
        "",
        "See results.json for per-query rankings, latency, models and provenance.",
    ]
    temporary = directory / "results.md.tmp"
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(directory / "results.md")
