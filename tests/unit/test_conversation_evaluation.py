import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from ragagent.domain.conversation_context import (
    ContextConfig,
    ContextMessage,
    QueryContextualization,
    ResolvedReferent,
    StructuredMemory,
)
from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    EvidenceRecord,
    QueryPlan,
    SearchResult,
    VerificationResponse,
)
from ragagent.evaluation.conversation import evaluate_conversation
from ragagent.evaluation.conversation_schema import (
    ConversationEvaluationCase,
    ConversationEvaluationDataset,
    ConversationEvaluationTurn,
)
from ragagent.evaluation.schema import CitationPair, RAGJudgment
from ragagent.graphs.state import AnalysisResult, ResearchPlan, SubTask
from ragagent.providers.chat import MockProvider, Usage
from ragagent.settings import Settings
from tests.unit.helpers import evidence
from tests.unit.test_generation_evaluation import RecordingProvider
from tests.unit.test_graphs import Search, claims, plan, verdict

T = TypeVar("T", bound=BaseModel)


def turn(identifier: str = "t1", **values: Any) -> ConversationEvaluationTurn:
    return ConversationEvaluationTurn(
        **{
            "id": identifier,
            "query": "What training method?",
            "question_type": "fact",
            "relevant_chunk_ids": ["c1"],
            "expected_answer": "The method uses contrastive training.",
            "required_aspects": ["method"],
            **values,
        }
    )


def dataset(case: ConversationEvaluationCase) -> ConversationEvaluationDataset:
    return ConversationEvaluationDataset(
        dataset_id="synthetic-conversation-test",
        label_source="synthetic",
        description="DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED",
        cases=[case],
    )


def judgment() -> RAGJudgment:
    return RAGJudgment(
        supported_pairs=[CitationPair(claim_id="c", evidence_id=evidence().evidence_id)],
        supported_claim_ids=["c"],
        answered_aspects=["method"],
    )


def rewrite(entity: str, source: str, *, mention: str = "it") -> QueryContextualization:
    return QueryContextualization(
        status="resolved",
        contextualized_query=f"What training method does {entity} use?",
        used_message_ids=[source],
        referents=[
            ResolvedReferent(
                mention=mention,
                resolved_text=entity,
                source_kind="message",
                source_id=source,
            )
        ],
    )


def agents(count: int, *, research: bool = False) -> dict[str, RecordingProvider]:
    return {
        "supervisor": RecordingProvider(
            [
                plan()
                if research
                else QueryPlan(queries=["contrastive"], required_aspects=["method"])
            ]
            * count
        ),
        "retriever": RecordingProvider([]),
        "analyst": RecordingProvider(
            [AnalysisResult(claims=claims()) if research else AnswerDraft(claims=claims())] * count
        ),
        "reviewer": RecordingProvider([verdict()] * count),
    }


def comparison_rewrite() -> QueryContextualization:
    return QueryContextualization(
        status="resolved",
        contextualized_query=(
            "Between Paper Alpha and Paper Beta, which has the largest sample size?"
        ),
        used_message_ids=["candidates"],
        referents=[
            ResolvedReferent(
                mention="Which one",
                resolved_text=paper,
                source_kind="message",
                source_id="candidates",
            )
            for paper in ("Paper Alpha", "Paper Beta")
        ],
    )


def comparison_case(mode: str = "rag") -> ConversationEvaluationCase:
    return ConversationEvaluationCase(
        id="compare-samples",
        mode=mode,
        dimensions=["context_resolution", "evidence_grounding", "memory_isolation"],
        seed_messages=[
            ContextMessage(
                id="candidates",
                ordinal=0,
                role="user",
                content=(
                    "Compare Paper Alpha and Paper Beta. Unverified false statement: "
                    "Paper Alpha has 500 participants, so it must have the largest sample."
                ),
            )
        ],
        turns=[
            turn(
                query="Which one has the largest sample size?",
                question_type="comparison",
                relevant_chunk_ids=["alpha-methods", "beta-methods"],
                relevant_paper_ids=["p-alpha", "p-beta"],
                expected_answer=(
                    "Paper Beta has the largest sample: 80 participants compared with "
                    "20 participants in Paper Alpha."
                ),
                required_aspects=["sample_size"],
                expected_context_terms=["Paper Alpha", "Paper Beta", "largest"],
                forbidden_answer_fragments=["500"],
            )
        ],
    )


FILTER_TEXT_FIELDS = (
    "paper_ids",
    "authors",
    "venues",
    "sections",
    "entity_types",
    "datasets",
    "methods",
    "metrics",
)
CREDENTIAL_LOCATIONS: list[tuple[str | int, ...]] = [
    ("dataset_id",),
    ("description",),
    ("cases", 0, "id"),
    ("cases", 0, "notes"),
    ("cases", 0, "seed_messages", 0, "id"),
    ("cases", 0, "seed_messages", 0, "status"),
    ("cases", 0, "seed_messages", 0, "content"),
    ("cases", 0, "memories", 0, "id"),
    ("cases", 0, "memories", 0, "key"),
    ("cases", 0, "memories", 0, "content"),
    *[
        ("cases", 0, "turns", 0, field)
        for field in ("id", "query", "notes", "expected_answer", "annotated_by", "annotated_at")
    ],
    *[
        ("cases", 0, "turns", 0, field, 0)
        for field in (
            "relevant_chunk_ids",
            "relevant_paper_ids",
            "required_aspects",
            "expected_context_terms",
            "forbidden_answer_fragments",
        )
    ],
    *[("cases", 0, "memories", 0, "filters", field, 0) for field in FILTER_TEXT_FIELDS],
    *[("cases", 0, "turns", 0, "filters", field, 0) for field in FILTER_TEXT_FIELDS],
]


def credential_payload(path: tuple[str | int, ...], credential: str) -> dict[str, Any]:
    """Construct synthetic rejected payloads; never read runtime credentials."""
    case = comparison_case()
    case.memories = [
        StructuredMemory(id="memory", kind="constraint", key="corpus", content="Focus on papers")
    ]
    value = dataset(case).model_dump(mode="json")
    value["cases"][0]["memories"][0]["filters"] = case.turns[0].filters.model_dump(mode="json")
    target: Any = value
    for component in path[:-1]:
        target = target[component]
    last = path[-1]
    if isinstance(target, list) and isinstance(last, int) and len(target) <= last:
        target.append(credential)
    else:
        target[last] = credential
    return value


@pytest.mark.parametrize("path", CREDENTIAL_LOCATIONS, ids=lambda path: ".".join(map(str, path)))
def test_conversation_evaluation_rejects_credentials_at_every_persistence_field(
    path: tuple[str | int, ...],
) -> None:
    credential = "sk-" + "syntheticfixture" * 2
    with pytest.raises(ValidationError, match="credential_content_not_allowed") as error:
        ConversationEvaluationDataset.model_validate(credential_payload(path, credential))
    # The error code contains no offending value; HTTP additionally uses the
    # application's input-free RequestValidationError response.
    assert credential not in str(error.value.errors(include_input=False))


@pytest.mark.parametrize(
    "credential",
    [
        "sk-" + "syntheticfixture" * 2,
        "ghp_" + "syntheticfixture" * 2,
        "github_pat_" + "syntheticfixture" * 2,
        "AKIA" + "C" * 16,
        "-----BEGIN PRIVATE KEY-----\nsynthetic fixture\n-----END PRIVATE KEY-----",
        "OPENAI_API_KEY=syntheticcredential12345",
        "api_key: syntheticcredential12345",
        "https://user:synthetic-password@example.org",
    ],
)
def test_conversation_evaluation_rejects_common_credential_shapes(credential: str) -> None:
    with pytest.raises(ValidationError, match="credential_content_not_allowed"):
        ConversationEvaluationDataset.model_validate(
            credential_payload(("cases", 0, "seed_messages", 0, "content"), credential)
        )


def test_direct_mutated_evaluation_cannot_bypass_persistence_guard() -> None:
    value = dataset(comparison_case())
    value.cases[0].turns[0].filters.authors = ["sk-" + "syntheticfixture" * 2]
    with pytest.raises(ValueError, match="^credential_content_not_allowed$"):
        value.runnable()


def test_evaluation_guard_preserves_ordinary_scientific_token_words() -> None:
    value = credential_payload(
        ("cases", 0, "notes"),
        "Study API key distribution, access_token frequency and tokenization methods.",
    )
    parsed = ConversationEvaluationDataset.model_validate(value)
    parsed.runnable()
    assert parsed.cases[0].notes == value["cases"][0]["notes"]


class ComparisonSearch(Search):
    """Test-only source fixture; both exact quotes are supplied by this turn's search."""

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        self.calls.append(plan)
        sources: list[EvidenceRecord] = []
        for name, size, index in (("Alpha", 20, 1), ("Beta", 80, 2)):
            text = f"Paper {name} enrolled {size} participants."
            source = evidence(
                f"{name.casefold()}-methods",
                f"00000000-0000-0000-0000-{index:012d}",
                f"p-{name.casefold()}",
            )
            sources.append(
                source.model_copy(
                    update={
                        "paper": source.paper.model_copy(update={"title": f"Paper {name}"}),
                        "content": text,
                        "quote": text,
                        "span_start": 0,
                        "span_end": len(text),
                    }
                )
            )
        return SearchResult(dense=[], lexical=[], fused=[], evidence=sources)


@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_comparison_followup_retains_both_candidates_and_retrieves_the_winner(
    mode: str, tmp_path: Path
) -> None:
    case = comparison_case(mode)
    sources = (await ComparisonSearch().search(QueryPlan(queries=["samples"]))).evidence
    claim = Claim(
        claim_id="sample-comparison",
        text=case.turns[0].expected_answer,
        evidence_ids=[source.evidence_id for source in sources],
        aspect="sample_size",
    )
    comparison_plan = (
        ResearchPlan(
            objective=case.turns[0].query,
            required_aspects=["sample_size"],
            subtasks=[
                SubTask(
                    task_id="samples",
                    aspect="sample_size",
                    question="Compare sample sizes for Paper Alpha and Paper Beta.",
                    queries=["Paper Alpha Paper Beta participants"],
                )
            ],
        )
        if mode == "research"
        else QueryPlan(
            queries=["Paper Alpha Paper Beta participants"], required_aspects=["sample_size"]
        )
    )
    workflow = {
        "supervisor": RecordingProvider([comparison_plan]),
        "retriever": RecordingProvider([comparison_rewrite()]),
        "analyst": RecordingProvider(
            [AnalysisResult(claims=[claim]) if mode == "research" else AnswerDraft(claims=[claim])]
        ),
        "reviewer": RecordingProvider(
            [
                VerificationResponse(
                    question_answered=True,
                    supported_pairs=[
                        ClaimEvidencePair(claim_id=claim.claim_id, evidence_id=eid)
                        for eid in claim.evidence_ids
                    ],
                    verdicts=[
                        ClaimVerdict(claim_id=claim.claim_id, supported=True, reason="quotes")
                    ],
                )
            ]
        ),
    }
    judge = RecordingProvider(
        [
            RAGJudgment(
                supported_pairs=[
                    CitationPair(claim_id=claim.claim_id, evidence_id=eid)
                    for eid in claim.evidence_ids
                ],
                supported_claim_ids=[claim.claim_id],
                answered_aspects=["sample_size"],
            )
        ]
    )
    search = ComparisonSearch()
    report = await evaluate_conversation(
        dataset(case), search, workflow, judge, Settings(), tmp_path
    )
    assert report["status"] == "completed"
    row = report["per_conversation"][0]["turns"][0]
    assert row["contextualized_query"] == comparison_rewrite().contextualized_query
    assert row["dimension_metrics"]["context_resolution"]["resolution_accuracy"] == 1.0
    assert row["dimension_metrics"]["memory_isolation"]["memory_isolation_accuracy"] == 1.0
    assert row["metrics"]["citation_precision"] == row["metrics"]["citation_recall"] == 1.0
    assert row["metrics"]["answer_completeness"] == 1.0
    assert {source["chunk_id"] for source in row["evidence"]} == {
        "alpha-methods",
        "beta-methods",
    }
    assert case.turns[0].expected_answer in row["actual_output"]
    assert "500" not in row["actual_output"]
    assert len(search.calls) == 1
    assert "500" not in json.dumps(workflow["analyst"].payloads)
    assert "recent_messages" not in workflow["analyst"].payloads[0]
    assert (
        workflow["supervisor"].payloads[0]["research_question" if mode == "research" else "query"]
        == comparison_rewrite().contextualized_query
    )


@pytest.mark.parametrize("defect", ["lost_comparison", "one_candidate"])
async def test_comparison_rewrite_cannot_choose_a_winner_from_false_history(
    defect: str, tmp_path: Path
) -> None:
    rewritten = comparison_rewrite()
    if defect == "lost_comparison":
        rewritten.contextualized_query = "Between Paper Alpha and Paper Beta, which has a sample?"
    else:
        rewritten.contextualized_query = "Which sample size is largest for Paper Alpha?"
        rewritten.referents = rewritten.referents[:1]
    workflow = agents(1)
    workflow["retriever"] = RecordingProvider([rewritten])
    search, judge = ComparisonSearch(), RecordingProvider([])
    report = await evaluate_conversation(
        dataset(comparison_case()), search, workflow, judge, Settings(), tmp_path
    )
    row = report["per_conversation"][0]["turns"][0]
    assert report["status"] == "failed" and report["summary_case_count"] == 0
    assert row["error_code"] == "context_resolution_invalid"
    assert row["failure_stage"] == "context"
    assert not search.calls and not workflow["supervisor"].calls
    assert not workflow["analyst"].calls and not workflow["reviewer"].calls
    assert not judge.calls
    assert report["usage"]["retriever"]["calls"] == 1


@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_actual_context_and_existing_graph_followup(mode: str, tmp_path: Path) -> None:
    case = ConversationEvaluationCase(
        id="follow-up",
        mode=mode,
        dimensions=["context_resolution", "evidence_grounding"],
        turns=[
            turn(),
            turn("t2", query="How does it train?", expected_context_terms=["contrastive"]),
        ],
    )
    workflow = agents(2, research=mode == "research")
    source = "assistant_" + hashlib.sha256(b"follow-up/t1").hexdigest()[:24]
    workflow["retriever"] = RecordingProvider([rewrite("contrastive training", source)])
    search = Search()
    report = await evaluate_conversation(
        dataset(case), search, workflow, MockProvider([judgment()] * 2), Settings(), tmp_path
    )
    assert report["status"] == "completed"
    assert report["summary"]["context_resolution"]["resolution_accuracy"] == 1.0
    assert report["summary"]["evidence_grounding"]["citation_precision"] == 1.0
    assert len(search.calls) == 2
    turns = report["per_conversation"][0]["turns"]
    assert turns[0]["context"]["rewrite_status"] == "standalone"
    assert turns[1]["original_query"] == "How does it train?"
    assert "contrastive training" in turns[1]["contextualized_query"]
    assert turns[1]["context"]["used_message_ids"] == [source]
    assert turns[1]["structural_checks"]["evidence_from_current_retrieval"] is True
    assert turns[1]["structural_checks"]["exact_source_provenance"] is True
    assert workflow["retriever"].calls == [QueryContextualization]
    # Context is available only to query rewriting, not the factual analyst.
    assert "recent_messages" in workflow["retriever"].payloads[0]
    assert "recent_messages" not in workflow["analyst"].payloads[1]
    assert workflow["analyst"].payloads[1]["evidence"][0]["quote"] == evidence().quote
    assert "NOT A BENCHMARK" in (tmp_path / "results.md").read_text()
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["manifest"]["corpus_snapshot"]["availability"] == "unavailable"
    assert (
        saved["manifest"]["model_configuration"]["conversation_context"]["rewrite_role"]
        == "retriever"
    )
    assert report["missing_dimensions"] == ["memory_isolation", "long_summary"]


async def test_false_history_is_context_not_scientific_evidence(tmp_path: Path) -> None:
    case = ConversationEvaluationCase(
        id="false-history",
        dimensions=["context_resolution", "evidence_grounding", "memory_isolation"],
        seed_messages=[
            ContextMessage(
                id="m1",
                ordinal=0,
                role="user",
                content="Paper Alpha uses memorization and reports 500 participants.",
            )
        ],
        turns=[
            turn(
                query="What training method does it use?",
                expected_context_terms=["Paper Alpha"],
                forbidden_answer_fragments=["memorization", "500"],
            )
        ],
    )
    workflow = agents(1)
    workflow["retriever"] = RecordingProvider([rewrite("Paper Alpha", "m1")])
    report = await evaluate_conversation(
        dataset(case), Search(), workflow, MockProvider([judgment()]), Settings(), tmp_path
    )
    result = report["per_conversation"][0]["turns"][0]
    assert report["summary"]["memory_isolation"]["memory_isolation_accuracy"] == 1.0
    assert result["actual_output"].startswith("The method uses contrastive training.")
    assert result["evidence"][0]["content"] == evidence().content
    assert "500" not in json.dumps(workflow["analyst"].payloads)
    assert "memorization" not in json.dumps(workflow["reviewer"].payloads)
    assert result["structural_checks"]["context_ids_not_evidence_ids"] is True


async def test_summary_preserves_an_old_referent_without_becoming_evidence(tmp_path: Path) -> None:
    case = ConversationEvaluationCase(
        id="long",
        dimensions=["long_summary", "context_resolution", "evidence_grounding"],
        seed_messages=[
            ContextMessage(
                id="m1", ordinal=0, role="user", content="Our research topic is Paper Alpha."
            ),
            *[
                ContextMessage(
                    id=f"m{i}",
                    ordinal=i - 1,
                    role="user",
                    content="Please continue reviewing literature.",
                )
                for i in range(2, 22)
            ],
        ],
        turns=[
            turn(
                query="What training method does it use?",
                expected_context_terms=["Paper Alpha"],
                expects_summary=True,
            )
        ],
    )
    workflow = agents(1)
    workflow["retriever"] = RecordingProvider([rewrite("Paper Alpha", "m1")])
    report = await evaluate_conversation(
        dataset(case),
        Search(),
        workflow,
        MockProvider([judgment()]),
        Settings(),
        tmp_path,
        context_config=ContextConfig(recent_message_limit=2),
    )
    row = report["per_conversation"][0]["turns"][0]
    assert row["context"]["summary_used"] is True
    assert "m1" not in row["context"]["recent_message_ids"]
    assert "m1" in row["summary"]["source_message_ids"]
    assert row["context"]["estimated_context_tokens"] <= row["context"]["max_context_tokens"]
    assert report["summary"]["long_summary"]["summary_context_accuracy"] == 1.0
    assert (
        "Unverified conversation excerpts"
        in workflow["retriever"].payloads[0]["conversation_summary"]["content"]
    )
    assert "conversation_summary" not in workflow["analyst"].payloads[0]


@pytest.mark.parametrize(
    "defect",
    [
        "unannotated",
        "no_gold",
        "no_answer",
        "no_aspects",
        "no_context",
        "no_isolation",
        "no_summary",
    ],
)
def test_missing_labels_are_rejected(defect: str) -> None:
    case = ConversationEvaluationCase(id="q", dimensions=["evidence_grounding"], turns=[turn()])
    value = dataset(case)
    if defect == "unannotated":
        value.label_source = "unannotated"
    elif defect == "no_gold":
        value.cases[0].turns[0].relevant_chunk_ids = []
    elif defect == "no_answer":
        value.cases[0].turns[0].expected_answer = ""
    elif defect == "no_aspects":
        value.cases[0].turns[0].required_aspects = []
    else:
        value.cases[0].dimensions = [
            {
                "no_context": "context_resolution",
                "no_isolation": "memory_isolation",
                "no_summary": "long_summary",
            }[defect]
        ]
    with pytest.raises(ValueError):
        value.runnable()


def test_human_labels_require_each_turn_annotation_provenance() -> None:
    value = dataset(
        ConversationEvaluationCase(id="q", dimensions=["evidence_grounding"], turns=[turn()])
    ).model_dump()
    value["label_source"] = "human"
    with pytest.raises(ValidationError, match="annotation_provenance"):
        ConversationEvaluationDataset.model_validate(value)


async def test_bad_rewrite_is_a_failed_evaluation_not_a_passing_refusal(tmp_path: Path) -> None:
    case = ConversationEvaluationCase(
        id="bad-context",
        dimensions=["memory_isolation"],
        seed_messages=[
            ContextMessage(
                id="m1", ordinal=0, role="user", content="Paper Alpha has 500 participants."
            )
        ],
        turns=[turn(query="What method does it use?", forbidden_answer_fragments=["500"])],
    )
    workflow = agents(1)
    poisoned = rewrite("Paper Alpha", "m1").model_copy(
        update={"contextualized_query": "What method does Paper Alpha with 500 participants use?"}
    )
    workflow["retriever"] = RecordingProvider([poisoned])
    search = Search()
    report = await evaluate_conversation(
        dataset(case), search, workflow, MockProvider([]), Settings(), tmp_path
    )
    assert report["status"] == "failed" and report["summary_case_count"] == 0
    assert report["summary"]["memory_isolation"] == {}
    row = report["per_conversation"][0]["turns"][0]
    assert row["error_code"] == "context_resolution_invalid"
    assert row["failure_stage"] == "context"
    assert not search.calls and not workflow["analyst"].calls
    assert report["usage"]["retriever"]["calls"] == 1


async def test_isolation_does_not_pass_when_final_answer_repeats_wrong_history(
    tmp_path: Path,
) -> None:
    case = ConversationEvaluationCase(
        id="wrong-answer",
        dimensions=["memory_isolation"],
        seed_messages=[
            ContextMessage(
                id="m1", ordinal=0, role="user", content="Paper Alpha uses memorization."
            )
        ],
        turns=[turn(query="What method does it use?", forbidden_answer_fragments=["memorization"])],
    )
    workflow = agents(1)
    workflow["retriever"] = RecordingProvider([rewrite("Paper Alpha", "m1")])
    # The test deliberately supplies an overly permissive verifier/judge; the
    # deterministic adversarial label still prevents an isolation pass.
    bad_claim = claims()[0].model_copy(update={"text": "The method uses memorization."})
    workflow["analyst"] = RecordingProvider([AnswerDraft(claims=[bad_claim])])
    report = await evaluate_conversation(
        dataset(case), Search(), workflow, MockProvider([judgment()]), Settings(), tmp_path
    )
    assert report["summary"]["memory_isolation"]["memory_isolation_accuracy"] == 0.0
    assert (
        report["per_conversation"][0]["turns"][0]["structural_checks"][
            "forbidden_history_answer_absent"
        ]
        is False
    )


async def test_partial_checkpoint_resumes_whole_failed_conversation_and_freezes_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ragagent.evaluation import conversation

    # Only the corpus availability guard is scripted here; graphs/context remain real.
    monkeypatch.setattr(
        conversation,
        "corpus_snapshot",
        lambda session: {"availability": "available", "hash": "fixed"},
    )
    monkeypatch.setattr(conversation, "verify_corpus_snapshot", lambda session, initial: None)
    value = dataset(
        ConversationEvaluationCase(id="first", dimensions=["evidence_grounding"], turns=[turn()])
    )
    value.cases.append(
        ConversationEvaluationCase(id="second", dimensions=["evidence_grounding"], turns=[turn()])
    )
    source = tmp_path / "first-attempt"
    first = await evaluate_conversation(
        value,
        Search(),
        agents(2),
        MockProvider([judgment(), {"invalid": "secret provider error text"}]),
        Settings(),
        source,
    )
    assert first["status"] == "partial"
    assert first["summary_case_count"] == first["failed_cases"] == 1
    assert first["per_conversation"][1]["turns"][0]["failure_stage"] == "judge"
    assert first["usage"]["judge"]["calls"] == 2
    assert "secret provider error text" not in (source / "results.json").read_text()
    workflow = agents(1)
    second = await evaluate_conversation(
        value,
        Search(),
        workflow,
        MockProvider([judgment()]),
        Settings(),
        tmp_path / "resumed",
        resume_directory=source,
        resume_usage={
            "retriever": {
                "calls": 3,
                "cost": None,
                "known_cost": 0.2,
                "unknown_cost_calls": 1,
                "prompt_tokens": 9,
                "completion_tokens": 0,
            }
        },
    )
    assert second["status"] == "completed"
    assert second["per_conversation"][0]["attempt_scope"] == "resumed"
    assert second["usage"]["judge"]["calls"] == 1
    assert len(workflow["analyst"].calls) == 1
    assert second["previous_attempt_usage"][0]["usage"]["retriever"]["known_cost"] == 0.2
    assert second["previous_attempt_usage"][0]["usage_source"] == "persisted_run"
    with pytest.raises(ValueError, match="resume_identity_mismatch"):
        await evaluate_conversation(
            value,
            Search(),
            agents(0),
            MockProvider([]),
            Settings(),
            tmp_path / "wrong-budget",
            context_config=ContextConfig(recent_message_limit=2),
            resume_directory=source,
        )


async def test_interrupt_keeps_unfinished_judge_charge_and_completed_evidence(
    tmp_path: Path,
) -> None:
    class InterruptedJudge(MockProvider):
        def __init__(self) -> None:
            super().__init__([])
            self.usage = Usage()

        async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
            self.usage.begin_call()
            raise KeyboardInterrupt()

    judge = InterruptedJudge()
    observer_records: list[int] = []

    def observer(usage: Usage) -> None:
        observer_records.append(usage.in_flight_calls)

    judge.usage.on_update = observer
    value = dataset(
        ConversationEvaluationCase(
            id="interrupt", dimensions=["evidence_grounding"], turns=[turn()]
        )
    )
    with pytest.raises(KeyboardInterrupt):
        await evaluate_conversation(value, Search(), agents(1), judge, Settings(), tmp_path)
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["status"] == "running" and saved["pending_cases"] == 1
    assert saved["summary_case_count"] == 0
    assert saved["usage"]["judge"]["in_flight_calls"] == 1
    assert saved["usage"]["judge"]["cost"] is None
    assert saved["per_conversation"][0]["turns"][0]["evidence"]
    assert saved["per_conversation"][0]["turns"][0]["usage"]["judge"]["unknown_cost_calls"] == 1
    assert judge.usage.on_update is observer and observer_records == [1]
