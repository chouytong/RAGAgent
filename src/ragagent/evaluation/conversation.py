"""Run bounded conversations through the production context and evidence pipelines."""

import hashlib
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ragagent.conversations.context import (
    CONTEXT_INSTRUCTION,
    CONTEXT_VERSION,
    ContextBuilder,
    contextualize,
)
from ragagent.domain.conversation_context import ContextConfig, ContextMessage, RollingSummary
from ragagent.domain.research import EvidenceRecord, QueryPlan, SearchResult
from ragagent.errors import ApplicationError
from ragagent.evaluation.artifacts import (
    canonical_hash,
    checkpoint_usage,
    corpus_snapshot,
    evaluation_status,
    manifest,
    previous_attempts,
    provider_snapshot,
    resume_results,
    retrieval_snapshot,
    usage_delta,
    usage_snapshot,
    verify_corpus_snapshot,
    workflow_usage,
    write_results,
)
from ragagent.evaluation.conversation_schema import (
    DIMENSIONS,
    ConversationEvaluationCase,
    ConversationEvaluationDataset,
    ConversationEvaluationTurn,
    validate_conversation_references,
)
from ragagent.evaluation.generation import JUDGE_PROMPT, JUDGE_VERSION, _generation_case
from ragagent.evaluation.metrics import average
from ragagent.providers.chat import ChatProvider
from ragagent.retrieval.evidence import exact_span
from ragagent.retrieval.service import SearchPort
from ragagent.settings import Settings


class _CurrentTurnSearch:
    """Observe actual retrieval; never substitute or manufacture search results."""

    def __init__(self, search: SearchPort) -> None:
        self.search_port = search
        self.evidence: dict[str, EvidenceRecord] = {}
        self.plans: list[dict[str, Any]] = []

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        result = await self.search_port.search(plan, rerank=rerank)
        self.plans.append(plan.model_dump(mode="json"))
        self.evidence.update({item.evidence_id: item for item in result.evidence})
        return result


def _contains_all(text: str, labels: list[str]) -> bool:
    return all(label.casefold() in text.casefold() for label in labels)


def _turn_metrics(
    turn: ConversationEvaluationTurn,
    row: dict[str, Any],
    search: _CurrentTurnSearch,
) -> None:
    evidence = [EvidenceRecord.model_validate(value) for value in row["evidence"]]
    context = row["context"]
    query = row["contextualized_query"]
    retrieved = search.evidence
    current = all(
        item.evidence_id in retrieved
        and canonical_hash(item.model_dump(mode="json"))
        == canonical_hash(retrieved[item.evidence_id].model_dump(mode="json"))
        for item in evidence
    )
    source_ids = set(context["history_message_ids"]) | set(context["memory_ids"])
    provenance = all(
        exact_span(item)
        and bool(item.paper.paper_id and item.chunk_id and item.section_id)
        and item.evidence_id not in source_ids
        and item.chunk_id not in source_ids
        for item in evidence
    )
    forbidden_absent = not any(
        phrase.casefold() in row["actual_output"].casefold()
        for phrase in turn.forbidden_answer_fragments
    )
    budget = context["estimated_context_tokens"] <= context["max_context_tokens"]
    resolved = _contains_all(query, turn.expected_context_terms)
    grounded = row["metrics"]["answer_completeness"] == 1.0 and (
        turn.expected_refusal or row["metrics"]["citation_precision"] == 1.0
    )
    checks = {
        "evidence_from_current_retrieval": current,
        "exact_source_provenance": provenance,
        "context_ids_not_evidence_ids": all(
            item.evidence_id not in source_ids and item.chunk_id not in source_ids
            for item in evidence
        ),
        "forbidden_history_answer_absent": forbidden_absent,
        "context_budget_respected": budget,
        "gold_context_terms_present": resolved if turn.expected_context_terms else None,
        "summary_used": context["summary_used"],
    }
    row["structural_checks"] = checks
    row["dimension_metrics"] = {
        "context_resolution": {
            "resolution_accuracy": float(resolved) if turn.expected_context_terms else None,
            "context_budget_compliance": float(budget),
        },
        "evidence_grounding": {
            key: row["metrics"][key]
            for key in (
                "citation_precision",
                "citation_recall",
                "citation_completeness",
                "unsupported_claim_rate",
                "answer_completeness",
                "refusal_correctness",
                "latency_ms",
                "judge_latency_ms",
            )
        },
        "memory_isolation": {
            # Empty/refused outputs cannot pass an ordinary factual isolation case.
            "memory_isolation_accuracy": float(
                current and provenance and forbidden_absent and grounded
            )
            if turn.forbidden_answer_fragments
            else None,
        },
        "long_summary": {
            "summary_context_accuracy": float(
                context["summary_used"] and resolved and budget and grounded
            )
            if turn.expects_summary
            else None,
            "summary_used_rate": float(context["summary_used"]) if turn.expects_summary else None,
        },
    }


async def _conversation_case(
    case: ConversationEvaluationCase,
    search: SearchPort,
    agents: Mapping[str, ChatProvider],
    judge: ChatProvider,
    settings: Settings,
    builder: ContextBuilder,
    row: dict[str, Any],
    checkpoint: Callable[[], None],
    providers: Mapping[str, Any],
) -> None:
    history = [message.model_copy(deep=True) for message in case.seed_messages]
    summary = RollingSummary()
    ordinal = max((message.ordinal for message in history), default=-1) + 1
    for turn in case.turns:
        before_usage = {name: usage_snapshot(provider) for name, provider in providers.items()}
        turn_row: dict[str, Any] = {
            "id": turn.id,
            "original_query": turn.query,
            "gold_labels": turn.model_dump(mode="json"),
            "evaluation_status": "running",
            "failure_stage": "context",
            "metrics": {},
            "evidence": [],
        }
        row["turns"].append(turn_row)
        start = time.perf_counter()
        try:
            bundle = builder.build(turn.query, history, summary, case.memories, turn.filters)
            summary = bundle.summary
            turn_row["context"] = bundle.metadata
            turn_row["summary"] = summary.model_dump(mode="json")
            checkpoint()
            resolved = await contextualize(bundle, agents["retriever"])
            turn_row.update(
                {
                    "context": resolved.metadata,
                    "contextualized_query": resolved.contextualized_query,
                    "effective_filters": resolved.filters.model_dump(mode="json"),
                    "context_latency_ms": (time.perf_counter() - start) * 1000,
                    "failure_stage": "workflow",
                }
            )
            current_search = _CurrentTurnSearch(search)
            # Reuse the original graph/evidence/judge implementation. Only the resolved
            # question and hard filters cross the conversation/evidence boundary.
            await _generation_case(
                turn.model_copy(
                    update={"query": resolved.contextualized_query, "filters": resolved.filters}
                ),
                current_search,
                agents,
                judge,
                settings,
                case.mode == "research",
                turn_row,
            )
            turn_row["retrieval_plans"] = current_search.plans
            _turn_metrics(turn, turn_row, current_search)
            # Only released answers/refusals join later turns; an intermediate analysis
            # or failed workflow draft is never inserted as assistant history.
            identity = hashlib.sha256(f"{case.id}/{turn.id}".encode()).hexdigest()[:24]
            history.extend(
                [
                    ContextMessage(
                        id=f"user_{identity}", ordinal=ordinal, role="user", content=turn.query
                    ),
                    ContextMessage(
                        id=f"assistant_{identity}",
                        ordinal=ordinal + 1,
                        role="assistant",
                        content=turn_row["actual_output"],
                        status=turn_row["status"],
                    ),
                ]
            )
            ordinal += 2
        except Exception as exc:
            turn_row.update(
                {
                    "evaluation_status": "failed",
                    "status": "failed",
                    "error_code": exc.code
                    if isinstance(exc, ApplicationError)
                    else "evaluation_case_failed",
                }
            )
            row["evaluation_status"] = "failed"
            row["error_code"] = turn_row["error_code"]
            # Continue with a fresh case, not an incoherent partial conversation.
            break
        finally:
            turn_row["usage"] = {
                name: usage_delta(before_usage[name], usage_snapshot(provider))
                for name, provider in providers.items()
            }
            turn_row["total_latency_ms"] = (time.perf_counter() - start) * 1000
            checkpoint()
    else:
        row["evaluation_status"] = "completed"
    row["pending_turns"] = len(case.turns) - len(row["turns"])
    row["summary"] = summary.model_dump(mode="json")


async def evaluate_conversation(
    dataset: ConversationEvaluationDataset,
    search: SearchPort,
    agents: Mapping[str, ChatProvider],
    judge: ChatProvider,
    settings: Settings,
    directory: Path,
    *,
    context_config: ContextConfig | None = None,
    resume_directory: Path | None = None,
    resume_usage: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dataset = dataset.model_copy(deep=True)
    settings = settings.model_copy(deep=True)
    config = (context_config or ContextConfig()).model_copy(deep=True)
    dataset.runnable()
    session = getattr(search, "session", None)
    session = session if isinstance(session, Session) else None
    if session is not None:
        validate_conversation_references(dataset, session)
    corpus = corpus_snapshot(session)
    provenance = manifest(
        dataset.generation_dataset(),
        settings,
        model_configuration={
            "agents": {name: provider_snapshot(provider) for name, provider in agents.items()},
            "conversation_context": {
                "configuration": config.model_dump(mode="json"),
                "context_version": CONTEXT_VERSION,
                "rewrite_role": "retriever",
                "prompt": CONTEXT_INSTRUCTION,
                "prompt_hash": hashlib.sha256(CONTEXT_INSTRUCTION.encode()).hexdigest(),
                "summary_type": "deterministic_lossy_excerpts_not_evidence",
            },
        },
        retrieval_configuration=retrieval_snapshot(search, settings),
        corpus=corpus,
    )
    provenance["dataset_hash"] = canonical_hash(dataset.model_dump(mode="json"))
    provenance["judge"] = {
        **provider_snapshot(judge),
        "evaluation_type": "MODEL_BASED",
        "prompt_version": JUDGE_VERSION,
        "prompt": JUDGE_PROMPT,
        "prompt_hash": hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest(),
    }
    previous = resume_results(resume_directory, "conversation", provenance)
    embedding = getattr(getattr(search, "dense", None), "embedder", None)
    providers = {**agents, "judge": judge, "embedding": embedding}
    initial_usage = {name: usage_snapshot(provider) for name, provider in providers.items()}
    rows = (
        [
            {**row, "attempt_scope": "resumed", "resumed_from": resume_directory.name}
            for row in previous.get("per_conversation", [])
            if row.get("evaluation_status") == "completed"
        ]
        if previous and resume_directory
        else []
    )
    retained_ids = {row["id"] for row in rows}
    coverage = {
        dimension: sum(dimension in case.dimensions for case in dataset.cases)
        for dimension in DIMENSIONS
    }
    report: dict[str, Any] = {
        "kind": "conversation",
        "manifest": provenance,
        "per_conversation": rows,
        "dimension_case_counts": coverage,
        "missing_dimensions": [dimension for dimension, count in coverage.items() if not count],
        "usage_scope": "current_attempt",
        "previous_attempt_usage": previous_attempts(previous, resume_usage),
        "corpus_verification": "pending",
    }

    def checkpoint() -> None:
        successful = [row for row in rows if row["evaluation_status"] == "completed"]
        failed = sum(row["evaluation_status"] == "failed" for row in rows)
        pending = len(dataset.cases) - len(successful) - failed
        usage = {
            name: usage_delta(initial_usage[name], usage_snapshot(provider))
            for name, provider in providers.items()
        }
        summary = {
            dimension: average(
                turn["dimension_metrics"][dimension]
                for row in successful
                if dimension in row["dimensions"]
                for turn in row["turns"]
                if "dimension_metrics" in turn
            )
            for dimension in DIMENSIONS
        }
        provenance["evidence_snapshot_hash"] = canonical_hash(
            [[turn.get("evidence", []) for turn in row["turns"]] for row in rows]
        )
        report.update(
            {
                "status": evaluation_status(len(successful), failed, pending),
                "summary": summary,
                "summary_case_count": len(successful),
                "dimension_evaluated_case_counts": {
                    dimension: sum(dimension in row["dimensions"] for row in successful)
                    for dimension in DIMENSIONS
                },
                "failed_cases": failed,
                "pending_cases": pending,
                "evaluated_cases": len(successful),
                "usage": usage,
                **workflow_usage(usage),
            }
        )
        if report["corpus_verification"] == "failed":
            report["status"] = "failed"
        elif report["corpus_verification"] == "pending" and report["status"] == "completed":
            report["status"] = "running"
        write_results(directory, report)

    with checkpoint_usage(providers, checkpoint):
        checkpoint()
        for case in dataset.cases:
            if case.id in retained_ids:
                continue
            before = {name: usage_snapshot(provider) for name, provider in providers.items()}
            row: dict[str, Any] = {
                "id": case.id,
                "mode": case.mode,
                "dimensions": case.dimensions,
                "evaluation_status": "running",
                "attempt_scope": "current",
                "turns": [],
            }
            rows.append(row)
            await _conversation_case(
                case,
                search,
                agents,
                judge,
                settings,
                ContextBuilder(config),
                row,
                checkpoint,
                providers,
            )
            row["usage"] = {
                name: usage_delta(before[name], usage_snapshot(provider))
                for name, provider in providers.items()
            }
            if row["evaluation_status"] == "failed" and session is not None:
                session.rollback()
            checkpoint()
        try:
            verify_corpus_snapshot(session, corpus)
            report["corpus_verification"] = "verified" if session is not None else "unavailable"
        except ApplicationError as exc:
            report["corpus_verification"], report["error_code"] = "failed", exc.code
        checkpoint()
        return report
