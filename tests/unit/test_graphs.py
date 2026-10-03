import pytest

from ragagent.domain.research import (
    AnswerDraft,
    CitationValidation,
    Claim,
    ClaimVerdict,
    MetadataFilter,
    QueryExpansion,
    QueryPlan,
    SearchResult,
    VerificationResponse,
)
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research, supervisor_route
from ragagent.graphs.state import (
    AnalysisResult,
    MultiAgentState,
    RAGState,
    ResearchPlan,
    ReviewResult,
    SubTask,
)
from ragagent.providers.chat import MockProvider
from tests.unit.helpers import evidence


class Search:
    def __init__(self, empty_rounds: int = 0) -> None:
        self.calls: list[QueryPlan] = []
        self.empty_rounds = empty_rounds

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        self.calls.append(plan)
        return SearchResult(
            dense=[],
            lexical=[],
            fused=[],
            evidence=[] if len(self.calls) <= self.empty_rounds else [evidence()],
        )


def claims() -> list[Claim]:
    return [
        Claim(
            claim_id="c",
            text="The method uses contrastive training.",
            evidence_ids=[evidence().evidence_id],
            aspect="method",
        )
    ]


def verdict(supported: bool = True) -> VerificationResponse:
    return VerificationResponse(
        question_answered=True,
        verdicts=[ClaimVerdict(claim_id="c", supported=supported, reason="quote")],
    )


def plan() -> ResearchPlan:
    return ResearchPlan(
        objective="Compare",
        required_aspects=["method"],
        subtasks=[
            SubTask(task_id="t", question="method?", aspect="method", queries=["contrastive"])
        ],
    )


async def test_rag_known_query_citation_regression() -> None:
    result = await build_rag(
        Search(),
        MockProvider([QueryPlan(queries=["contrastive"], required_aspects=["method"])]),
        MockProvider([]),
        MockProvider([AnswerDraft(claims=claims())]),
        MockProvider([verdict()]),
    ).ainvoke(RAGState(query="What training method?"))
    state = RAGState.model_validate(result)
    assert state.status == "completed"
    assert "[E:" + evidence().evidence_id + "]" in state.answer


async def test_rag_retry_limit_and_filter_preservation() -> None:
    search = Search(empty_rounds=99)
    graph = build_rag(
        search,
        MockProvider(
            [QueryPlan(queries=["q"], filters=MetadataFilter(paper_ids=["unauthorized"]))]
        ),
        MockProvider([QueryExpansion(queries=["q1"]), QueryExpansion(queries=["q2"])]),
        MockProvider([]),
        MockProvider([]),
        max_retries=2,
    )
    state = RAGState.model_validate(
        await graph.ainvoke(
            RAGState(query="q", filters=MetadataFilter(paper_ids=["requested"])),
            {"recursion_limit": 50},
        )
    )
    assert state.status == "insufficient_evidence" and state.retrieval_attempt == 3
    assert all(p.filters.paper_ids == ["requested"] for p in search.calls)


async def test_rag_semantic_rejection_refuses() -> None:
    graph = build_rag(
        Search(),
        MockProvider([QueryPlan(queries=["q"])]),
        MockProvider([]),
        MockProvider([AnswerDraft(claims=claims())]),
        MockProvider([verdict(False)]),
        max_retries=0,
    )
    state = RAGState.model_validate(await graph.ainvoke(RAGState(query="q")))
    assert state.status == "insufficient_evidence" and not state.claims


async def test_research_revision_routes_to_analysis_without_retrieval() -> None:
    search = Search()
    graph = build_research(
        search,
        MockProvider([plan()]),
        MockProvider([]),
        MockProvider([AnalysisResult(claims=claims()), AnalysisResult(claims=claims())]),
        MockProvider([verdict(False), verdict()]),
    )
    result = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="method?"), {"recursion_limit": 100})
    )
    assert result.status == "completed" and result.revision_count == 1
    assert (
        len(search.calls) == 1 and result.review_result and result.review_result.decision == "PASS"
    )


async def test_research_more_evidence_and_refusal() -> None:
    search = Search(empty_rounds=1)
    graph = build_research(
        search,
        MockProvider([plan(), plan()]),
        MockProvider([QueryExpansion(queries=["expanded"])]),
        MockProvider([AnalysisResult(claims=claims())]),
        MockProvider([verdict()]),
    )
    state = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="q"), {"recursion_limit": 100})
    )
    assert state.status == "completed" and state.retrieval_count == 2
    graph = build_research(
        Search(empty_rounds=99),
        MockProvider([plan()]),
        MockProvider([]),
        MockProvider([]),
        MockProvider([]),
        max_retrievals=1,
    )
    result = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="q"))
    )
    assert result.status == "insufficient_evidence"


async def test_iteration_cap_before_next_agent() -> None:
    graph = build_research(
        Search(),
        MockProvider([plan()]),
        MockProvider([]),
        MockProvider([]),
        MockProvider([]),
        max_iterations=1,
    )
    result = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="q"))
    )
    assert result.iteration == 1 and result.status == "insufficient_evidence"


@pytest.mark.parametrize(
    "decision,expected",
    [("PASS", "finish"), ("NEED_MORE_EVIDENCE", "retriever"), ("NEED_REVISION", "analysis")],
)
def test_supervisor_routes(decision: str, expected: str) -> None:
    state = MultiAgentState(
        research_question="q",
        review_result=ReviewResult(
            decision=decision, validation=CitationValidation(valid=decision == "PASS")
        ),
    )
    assert supervisor_route(state, 3, 2, 12) == expected
    if decision != "PASS":
        state.iteration = 12
        assert supervisor_route(state, 3, 2, 12) == "stop"
