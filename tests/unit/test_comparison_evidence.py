"""Scripted comparison contracts; no model quality or benchmark claims."""

import pytest

from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    ComparisonEntityCoverage,
    EvidenceRecord,
    QueryExpansion,
    QueryPlan,
    Sufficiency,
    VerificationResponse,
)
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import AnalysisResult, MultiAgentState, RAGState, ResearchPlan, SubTask
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.evidence import evidence_gate, verify_claims
from tests.unit.helpers import evidence
from tests.unit.test_graphs import RoundSearch

QUESTION = "Compare Method A and Method B's accuracy."
ENTITIES = ["Method A", "Method B"]


def sources(layout: str) -> list[EvidenceRecord]:
    if layout == "single_chunk":
        result = [evidence()]
        texts = ["Method A achieved accuracy 90%. Method B achieved accuracy 85%."]
    else:
        result = [
            evidence(),
            evidence(
                "c2",
                "00000000-0000-0000-0000-000000000002",
                "p2" if layout == "multi_paper" else "p1",
            ),
        ]
        texts = ["Method A achieved accuracy 90%.", "Method B achieved accuracy 85%."]
    for item, text in zip(result, texts, strict=True):
        item.content = item.quote = text
        item.span_end = len(text)
    return result


def comparison_claim(items: list[EvidenceRecord]) -> Claim:
    return Claim(
        claim_id="accuracy",
        text="Method A's accuracy exceeds Method B's by 5 percentage points.",
        evidence_ids=[item.evidence_id for item in items],
        aspect="accuracy",
    )


def comparison_response(items: list[EvidenceRecord]) -> VerificationResponse:
    pairs = [ClaimEvidencePair(claim_id="accuracy", evidence_id=item.evidence_id) for item in items]
    return VerificationResponse(
        question_answered=True,
        verdicts=[
            ClaimVerdict(claim_id="accuracy", supported=True, reason="Compared exact values")
        ],
        supported_pairs=pairs,
        comparison_entities=[
            ComparisonEntityCoverage(
                entity="Method A", supported=True, supporting_pairs=[pairs[0]]
            ),
            ComparisonEntityCoverage(
                entity="Method B", supported=True, supporting_pairs=[pairs[-1]]
            ),
        ],
    )


def research_plan() -> ResearchPlan:
    return ResearchPlan(
        objective=QUESTION,
        question_type="comparison",
        comparison_entities=ENTITIES,
        required_aspects=["accuracy"],
        subtasks=[
            SubTask(task_id="accuracy", question=QUESTION, aspect="accuracy", queries=[QUESTION])
        ],
    )


async def execute_comparison(
    mode: str,
    items: list[EvidenceRecord],
    response: VerificationResponse,
) -> tuple[RAGState | MultiAgentState, MockProvider]:
    reviewer = MockProvider([response])
    claim = comparison_claim(items)
    if mode == "rag":
        state: RAGState | MultiAgentState = RAGState.model_validate(
            await build_rag(
                RoundSearch([items]),
                MockProvider(
                    [
                        QueryPlan(
                            queries=[QUESTION],
                            question_type="comparison",
                            comparison_entities=ENTITIES,
                            required_aspects=["accuracy"],
                        )
                    ]
                ),
                MockProvider([]),
                MockProvider([AnswerDraft(claims=[claim])]),
                reviewer,
                max_retries=0,
            ).ainvoke(RAGState(query=QUESTION))
        )
    else:
        state = MultiAgentState.model_validate(
            await build_research(
                RoundSearch([items]),
                MockProvider([research_plan()]),
                MockProvider([]),
                MockProvider([AnalysisResult(claims=[claim])]),
                reviewer,
                max_retrievals=1,
                max_revisions=0,
            ).ainvoke(MultiAgentState(research_question=QUESTION))
        )
    return state, reviewer


@pytest.mark.parametrize("mode", ["rag", "research"])
@pytest.mark.parametrize("layout", ["single_chunk", "single_paper", "multi_paper"])
async def test_comparison_uses_supported_entities_instead_of_paper_count(
    mode: str, layout: str
) -> None:
    items = sources(layout)
    state, reviewer = await execute_comparison(mode, items, comparison_response(items))
    assert state.status == "completed"
    output = state.answer if isinstance(state, RAGState) else state.draft_report
    assert comparison_claim(items).text in output
    assert all(f"[E:{item.evidence_id}]" in output for item in items)
    validation = (
        state.citation_validation
        if isinstance(state, RAGState)
        else state.review_result.validation
        if state.review_result
        else None
    )
    assert validation is not None and validation.valid and not validation.comparison_errors
    assert {item.entity for item in validation.comparison_entities} == set(ENTITIES)
    assert reviewer.calls == [VerificationResponse]
    if isinstance(state, RAGState):
        assert state.sufficiency is not None and state.sufficiency.status == Sufficiency.SUFFICIENT
        assert state.sufficiency.paper_count == (2 if layout == "multi_paper" else 1)


@pytest.mark.parametrize("mode", ["rag", "research"])
@pytest.mark.parametrize(
    "defect", ["one_entity", "missing_coverage", "unsupported_entity", "false_claim"]
)
async def test_comparison_refuses_without_semantic_entity_and_claim_support(
    mode: str, defect: str
) -> None:
    items = sources("single_chunk")
    response = comparison_response(items)
    if defect == "one_entity":
        response.comparison_entities.pop()
    elif defect == "missing_coverage":
        response.comparison_entities = []
    elif defect == "unsupported_entity":
        # Both names occur verbatim; the reviewer does not support B's compared fact.
        response.comparison_entities[-1].supported = False
    else:
        response.verdicts[0].supported = False
    state, reviewer = await execute_comparison(mode, items, response)
    assert state.status == "insufficient_evidence"
    output = state.answer if isinstance(state, RAGState) else state.draft_report
    assert comparison_claim(items).text not in output and "[E:" not in output
    assert reviewer.calls == [VerificationResponse]
    validation = (
        state.citation_validation
        if isinstance(state, RAGState)
        else state.review_result.validation
        if state.review_result
        else None
    )
    assert validation is not None and not validation.valid and validation.comparison_errors


@pytest.mark.parametrize(
    "defect",
    [
        "invented_name",
        "wrong_pair_source",
        "duplicate_name",
        "unknown_pair",
        "duplicate_pair",
        "wrong_targets",
    ],
)
async def test_entity_coverage_cannot_override_provenance_and_supported_pairs(defect: str) -> None:
    items = sources("multi_paper")
    response = comparison_response(items)
    targets = ENTITIES
    if defect == "invented_name":
        response.comparison_entities[-1].entity = "Method C"
    elif defect == "wrong_pair_source":
        response.comparison_entities[-1].supporting_pairs = [response.supported_pairs[0]]
    elif defect == "duplicate_name":
        response.comparison_entities[-1].entity = " method   a "
    elif defect == "unknown_pair":
        response.comparison_entities[-1].supporting_pairs = [
            ClaimEvidencePair(claim_id="unverified", evidence_id=items[-1].evidence_id)
        ]
    elif defect == "duplicate_pair":
        response.comparison_entities[-1].supporting_pairs *= 2
    else:
        targets = ["Method A", "Method C"]
    validation = await verify_claims(
        [comparison_claim(items)],
        items,
        ["accuracy"],
        MockProvider([response]),
        QUESTION,
        comparison=True,
        comparison_entities=targets,
    )
    assert not validation.valid and validation.comparison_errors
    assert "comparison_entities" in validation.missing_aspects
    assert (
        evidence_gate(
            QueryPlan(queries=[QUESTION], question_type="comparison", comparison_entities=targets),
            items,
            validation,
        ).status
        == Sufficiency.PARTIAL
    )


async def test_two_papers_do_not_make_one_supported_entity_a_complete_comparison() -> None:
    items = sources("multi_paper")
    response = comparison_response(items)
    response.comparison_entities.pop()
    state, _ = await execute_comparison("rag", items, response)
    assert state.status == "insufficient_evidence"
    assert isinstance(state, RAGState) and state.sufficiency is not None
    assert state.sufficiency.paper_count == 2 and state.sufficiency.status == Sufficiency.PARTIAL


async def test_empty_comparison_evidence_still_refuses_without_analysis() -> None:
    analyst, reviewer = MockProvider([]), MockProvider([])
    state = RAGState.model_validate(
        await build_rag(
            RoundSearch([[]]),
            MockProvider([QueryPlan(queries=[QUESTION], question_type="comparison")]),
            MockProvider([]),
            analyst,
            reviewer,
            max_retries=0,
        ).ainvoke(RAGState(query=QUESTION))
    )
    assert state.status == "insufficient_evidence" and not analyst.calls and not reviewer.calls


async def test_research_replan_cannot_drop_comparison_type_or_requested_entities() -> None:
    items = sources("single_chunk")
    original = research_plan()
    relaxed = original.model_copy(deep=True)
    relaxed.question_type = "fact"
    relaxed.comparison_entities = []
    relaxed.subtasks[0].question = "Revised focused accuracy search"
    response = comparison_response(items)
    response.comparison_entities.pop()
    search = RoundSearch([items, items])
    research = MultiAgentState.model_validate(
        await build_research(
            search,
            MockProvider([original, relaxed]),
            MockProvider([QueryExpansion(queries=["accuracy"])]),
            MockProvider([AnalysisResult(claims=[comparison_claim(items)])] * 2),
            MockProvider([response, response]),
            max_retrievals=2,
            max_revisions=0,
        ).ainvoke(MultiAgentState(research_question=QUESTION))
    )
    assert len(search.calls) == 2
    assert research.status == "insufficient_evidence" and "[E:" not in research.draft_report
    assert research.research_plan is not None
    assert research.research_plan.question_type == "comparison"
    assert research.research_plan.comparison_entities == ENTITIES
    assert research.review_result is not None and not research.review_result.validation.valid
    assert "comparison_entities" in research.review_result.validation.missing_aspects
