import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ragagent.domain.research import QueryPlan
from ragagent.evaluation.artifacts import (
    corpus_snapshot,
    manifest,
    retrieval_snapshot,
    verify_corpus_snapshot,
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
    rows: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    search = HybridRetriever(
        session,
        search.dense.embedder,
        search.reranker,
        search.top_n,
        max(10, search.top_k),
        search.fusion.k,
    )
    provenance["retrieval_configuration"]["evaluation_top_k"] = search.top_k
    for case in dataset.cases:
        plan = QueryPlan(
            queries=[case.query], filters=case.filters, question_type=case.question_type
        )
        for mode in modes:
            start = time.perf_counter()
            if mode == "dense":
                candidates = await search.dense.search(case.query, case.filters, 10)
                evidence = [c.evidence for c in candidates]
                ranking = [e.chunk_id for e in evidence]
            elif mode == "lexical":
                candidates = await search.lexical.search(case.query, case.filters, 10)
                evidence = [c.evidence for c in candidates]
                ranking = [e.chunk_id for e in evidence]
            else:
                result = await search.search(plan, rerank=mode == "hybrid_rerank")
                ranking = (
                    [e.chunk_id for e in result.evidence]
                    if mode == "hybrid_rerank"
                    else [c.evidence.chunk_id for c in result.fused[:10]]
                )
                evidence = (
                    result.evidence
                    if mode == "hybrid_rerank"
                    else [c.evidence for c in result.fused[:10]]
                )
            latency = (time.perf_counter() - start) * 1000
            rows[mode].append(
                {
                    "id": case.id,
                    "ranking": ranking,
                    "evidence": [e.model_dump(mode="json") for e in evidence],
                    "gold_labels": case.model_dump(mode="json"),
                    "metrics": {
                        **retrieval_metrics(ranking, set(case.relevant_chunk_ids)),
                        "latency_ms": latency,
                    },
                }
            )
    verify_corpus_snapshot(session, corpus)
    provenance["active_retrieval_adapters"] = {
        "embedder": type(search.dense.embedder).__name__,
        "embedding_fingerprint": search.dense.embedder.fingerprint,
        "reranker": type(search.reranker).__name__,
    }

    report = {
        "kind": "retrieval",
        "manifest": provenance,
        "per_query": rows,
        "summary": {
            mode: average(row["metrics"] for row in values) for mode, values in rows.items()
        },
    }
    write_results(directory, report)
    return report
