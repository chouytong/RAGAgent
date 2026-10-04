import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ragagent.domain.research import QueryPlan
from ragagent.errors import ApplicationError
from ragagent.evaluation.artifacts import (
    checkpoint_usage,
    corpus_snapshot,
    evaluation_status,
    manifest,
    previous_attempts,
    resume_results,
    retrieval_snapshot,
    usage_delta,
    usage_snapshot,
    verify_corpus_snapshot,
    workflow_usage,
    write_results,
)
from ragagent.evaluation.metrics import average, retrieval_metrics
from ragagent.evaluation.schema import EvaluationDataset
from ragagent.evaluation.validation import validate_references
from ragagent.retrieval.service import HybridRetriever
from ragagent.settings import Settings


async def evaluate_retrieval(
    dataset: EvaluationDataset,
    search: HybridRetriever,
    session: Session,
    settings: Settings,
    directory: Path,
    *,
    resume_directory: Path | None = None,
    resume_usage: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dataset = dataset.model_copy(deep=True)
    settings = settings.model_copy(deep=True)
    dataset.runnable()
    validate_references(dataset, session)
    corpus = corpus_snapshot(session)
    provenance = manifest(
        dataset,
        settings,
        retrieval_configuration=retrieval_snapshot(search, settings),
        corpus=corpus,
    )
    modes = ["dense", "lexical", "hybrid", "hybrid_rerank"]
    search = HybridRetriever(
        session,
        search.dense.embedder,
        search.reranker,
        search.top_n,
        max(10, search.top_k),
        search.fusion.k,
    )
    provenance["retrieval_configuration"]["evaluation_top_k"] = search.top_k
    previous = resume_results(resume_directory, "retrieval", provenance)
    rows: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    if previous and resume_directory:
        for mode in modes:
            rows[mode] = [
                {**row, "attempt_scope": "resumed", "resumed_from": resume_directory.name}
                for row in previous["per_query"].get(mode, [])
                if row.get("evaluation_status") == "completed"
            ]
    retained = {(mode, row["id"]) for mode in modes for row in rows[mode]}
    embedding = search.dense.embedder
    initial_usage = usage_snapshot(embedding)
    report: dict[str, Any] = {
        "kind": "retrieval",
        "manifest": provenance,
        "per_query": rows,
        "usage_scope": "current_attempt",
        "previous_attempt_usage": previous_attempts(previous, resume_usage),
        "corpus_verification": "pending",
    }

    def checkpoint() -> None:
        all_rows = [row for mode in modes for row in rows[mode]]
        by_case: dict[str, list[dict[str, Any]]] = {case.id: [] for case in dataset.cases}
        for row in all_rows:
            by_case[row["id"]].append(row)
        finished = [values for values in by_case.values() if len(values) == len(modes)]
        failed = sum(
            any(row["evaluation_status"] == "failed" for row in values) for values in finished
        )
        pending = len(dataset.cases) - len(finished)
        successful = len(finished) - failed
        usage = {"embedding": usage_delta(initial_usage, usage_snapshot(embedding))}
        report.update(
            {
                "status": evaluation_status(successful, failed, pending),
                "summary": {
                    mode: average(
                        row["metrics"]
                        for row in rows[mode]
                        if row["evaluation_status"] == "completed"
                    )
                    for mode in modes
                },
                "summary_case_count": {
                    mode: sum(row["evaluation_status"] == "completed" for row in rows[mode])
                    for mode in modes
                },
                "evaluated_cases": successful,
                "failed_cases": failed,
                "pending_cases": pending,
                "usage": usage,
                **workflow_usage(usage),
            }
        )
        if report["corpus_verification"] == "failed":
            report["status"] = "failed"
        elif report["corpus_verification"] == "pending" and report["status"] == "completed":
            report["status"] = "running"
        write_results(directory, report)

    with checkpoint_usage({"embedding": embedding}, checkpoint):
        checkpoint()
        for case in dataset.cases:
            plan = QueryPlan(
                queries=[case.query], filters=case.filters, question_type=case.question_type
            )
            for mode in modes:
                if (mode, case.id) in retained:
                    continue
                start = time.perf_counter()
                before_usage = usage_snapshot(embedding)
                row: dict[str, Any] = {
                    "id": case.id,
                    "evaluation_status": "failed",
                    "attempt_scope": "current",
                    "gold_labels": case.model_dump(mode="json"),
                    "metrics": {},
                }
                try:
                    if mode == "dense":
                        candidates = await search.dense.search(case.query, case.filters, 10)
                        evidence = [candidate.evidence for candidate in candidates]
                    elif mode == "lexical":
                        candidates = await search.lexical.search(case.query, case.filters, 10)
                        evidence = [candidate.evidence for candidate in candidates]
                    else:
                        result = await search.search(plan, rerank=mode == "hybrid_rerank")
                        evidence = (
                            result.evidence
                            if mode == "hybrid_rerank"
                            else [candidate.evidence for candidate in result.fused[:10]]
                        )
                    ranking = [item.chunk_id for item in evidence]
                    row.update(
                        {
                            "ranking": ranking,
                            "evidence": [item.model_dump(mode="json") for item in evidence],
                            "metrics": {
                                **retrieval_metrics(ranking, set(case.relevant_chunk_ids)),
                                "latency_ms": (time.perf_counter() - start) * 1000,
                            },
                            "evaluation_status": "completed",
                        }
                    )
                except Exception as exc:
                    row["error_code"] = (
                        exc.code if isinstance(exc, ApplicationError) else "evaluation_case_failed"
                    )
                    session.rollback()
                row["attempt_latency_ms"] = (time.perf_counter() - start) * 1000
                row["usage"] = {"embedding": usage_delta(before_usage, usage_snapshot(embedding))}
                rows[mode].append(row)
                checkpoint()
        try:
            verify_corpus_snapshot(session, corpus)
            report["corpus_verification"] = "verified"
        except ApplicationError as exc:
            report["corpus_verification"], report["error_code"] = "failed", exc.code
        checkpoint()
        return report
