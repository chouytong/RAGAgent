from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ragagent.domain.research import (
    AnswerDraft,
    CitationValidation,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    EvidenceRecord,
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
from ragagent.retrieval.evidence import verify_claims
from tests.unit.helpers import evidence

T = TypeVar("T", bound=BaseModel)


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
        supported_pairs=[ClaimEvidencePair(claim_id="c", evidence_id=evidence().evidence_id)]
        if supported
        else [],
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


class RoundSearch:
    def __init__(self, rounds: list[list[EvidenceRecord]]) -> None:
        self.rounds = iter(rounds)
        self.calls: list[QueryPlan] = []

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        self.calls.append(plan)
        return SearchResult(dense=[], lexical=[], fused=[], evidence=next(self.rounds))


class CapturingProvider(MockProvider):
    def __init__(self, responses: list[BaseModel | dict[str, Any]]) -> None:
        super().__init__(responses)
        self.payloads: list[dict[str, Any]] = []

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.payloads.append(payload)
        return await super().complete(instruction, payload, schema)


def dataset_evidence() -> EvidenceRecord:
    text = "The evaluation dataset is SciFact."
    return evidence("dataset", "00000000-0000-0000-0000-000000000002").model_copy(
        update={"content": text, "quote": text, "span_end": len(text)}
    )


def dataset_claim() -> Claim:
    item = dataset_evidence()
    return Claim(
        claim_id="dataset", text=item.quote, evidence_ids=[item.evidence_id], aspect="dataset"
    )


def aspect_verdict(selected: list[Claim], missing: list[str]) -> VerificationResponse:
    return VerificationResponse(
        supported_pairs=[
            ClaimEvidencePair(claim_id=c.claim_id, evidence_id=eid)
            for c in selected
            for eid in c.evidence_ids
        ],
        question_answered=not missing,
        missing_aspects=missing,
        verdicts=[
            ClaimVerdict(claim_id=c.claim_id, supported=True, reason="quote") for c in selected
        ],
    )


async def test_rag_expansion_retains_evidence_for_previously_supported_aspects() -> None:
    original = claims()
    combined = original + [dataset_claim()]
    search = RoundSearch([[evidence()], [dataset_evidence()]])
    analyst = CapturingProvider([AnswerDraft(claims=original), AnswerDraft(claims=combined)])
    graph = build_rag(
        search,
        MockProvider(
            [QueryPlan(queries=[f"q{i}" for i in range(6)], required_aspects=["method", "dataset"])]
        ),
        MockProvider([QueryExpansion(queries=[f"expanded{i}" for i in range(6)])]),
        analyst,
        MockProvider([aspect_verdict(original, ["dataset"]), aspect_verdict(combined, [])]),
        max_retries=1,
    )
    state = RAGState.model_validate(await graph.ainvoke(RAGState(query="Method and dataset?")))
    assert state.status == "completed" and state.retrieval_attempt == 2
    assert {e.evidence_id for e in state.reranked_evidence} == {
        evidence().evidence_id,
        dataset_evidence().evidence_id,
    }
    assert len(analyst.payloads[1]["evidence"]) == 2
    assert search.calls[0].queries != search.calls[1].queries


async def test_rag_evidence_budget_preserves_prior_evidence_and_reports_exhaustion() -> None:
    original = claims()
    graph = build_rag(
        RoundSearch([[evidence()], [dataset_evidence()]]),
        MockProvider([QueryPlan(queries=["q"], required_aspects=["method", "dataset"])]),
        MockProvider([QueryExpansion(queries=["dataset"])]),
        MockProvider([AnswerDraft(claims=original), AnswerDraft(claims=original)]),
        MockProvider(
            [aspect_verdict(original, ["dataset"]), aspect_verdict(original, ["dataset"])]
        ),
        max_retries=1,
        max_evidence_records=1,
    )
    state = RAGState.model_validate(await graph.ainvoke(RAGState(query="Method and dataset?")))
    assert state.status == "insufficient_evidence" and state.retrieval_attempt == 2
    assert state.reranked_evidence == [evidence()]
    assert state.errors == ["evidence_budget_exhausted"]


async def test_rag_threshold_limits_generation_and_citation_eligibility() -> None:
    low = dataset_evidence().model_copy(update={"scores": {"rerank": -5.0}})
    analyst = CapturingProvider([AnswerDraft(claims=[dataset_claim()])])
    reviewer = MockProvider([])
    graph = build_rag(
        RoundSearch([[evidence(), low]]),
        MockProvider([QueryPlan(queries=["q"], required_aspects=["dataset"])]),
        MockProvider([]),
        analyst,
        reviewer,
        max_retries=0,
    )
    state = RAGState.model_validate(await graph.ainvoke(RAGState(query="Dataset?")))
    assert state.status == "insufficient_evidence"
    assert [e.evidence_id for e in state.reranked_evidence] == [evidence().evidence_id]
    assert [e["evidence_id"] for e in analyst.payloads[0]["evidence"]] == [evidence().evidence_id]
    assert not reviewer.calls


@pytest.mark.parametrize("change_completed_task", [False, True])
async def test_research_replan_preserves_only_unchanged_completed_tasks(
    change_completed_task: bool,
) -> None:
    before = ResearchPlan(
        objective="Compare",
        required_aspects=["method", "dataset"],
        subtasks=[
            SubTask(task_id="t1", question="Method?", aspect="method", queries=["method"]),
            SubTask(task_id="t2", question="Dataset?", aspect="dataset", queries=["missing"]),
        ],
    )
    after = ResearchPlan(
        objective="Compare",
        required_aspects=["method", "dataset"],
        subtasks=[
            SubTask(task_id="t1", question="Dataset?", aspect="dataset", queries=["dataset"])
            if change_completed_task
            else before.subtasks[0],
            SubTask(task_id="t2", question="Method?", aspect="method", queries=["method"])
            if change_completed_task
            else SubTask(task_id="t2", question="Dataset?", aspect="dataset", queries=["dataset"]),
        ],
    )
    rounds = [[evidence()], [], [dataset_evidence()]]
    if change_completed_task:
        rounds.append([evidence()])
    search = RoundSearch(rounds)
    combined = claims() + [dataset_claim()]
    graph = build_research(
        search,
        MockProvider([before, after]),
        MockProvider([QueryExpansion(queries=["expanded"])] * (1 + int(change_completed_task))),
        MockProvider([AnalysisResult(claims=claims()), AnalysisResult(claims=combined)]),
        MockProvider([aspect_verdict(claims(), ["dataset"]), aspect_verdict(combined, [])]),
        max_retrievals=2,
    )
    state = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="Method and dataset?"))
    )
    assert state.status == "completed" and state.retrieval_count == 2
    assert state.completed_tasks == ["t1", "t2"]
    assert "dataset" in search.calls[2].queries
    assert len(search.calls) == 3 + int(change_completed_task)


async def test_research_threshold_rejects_low_evidence_before_task_completion() -> None:
    low = evidence().model_copy(update={"scores": {"rerank": 0.5}})
    analyst = MockProvider([])
    reviewer = MockProvider([])
    graph = build_research(
        RoundSearch([[low]]),
        MockProvider([plan()]),
        MockProvider([]),
        analyst,
        reviewer,
        max_retrievals=1,
        min_rerank_score=1.0,
    )
    state = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="Method?"))
    )
    assert state.status == "insufficient_evidence"
    assert not state.evidence_pool and not state.completed_tasks
    assert not analyst.calls and not reviewer.calls


async def test_research_cannot_release_claim_citing_filtered_evidence() -> None:
    low = dataset_evidence().model_copy(update={"scores": {"rerank": 0.5}})
    analyst = CapturingProvider([AnalysisResult(claims=[dataset_claim()])])
    reviewer = MockProvider([])
    graph = build_research(
        RoundSearch([[evidence(), low]]),
        MockProvider([plan()]),
        MockProvider([]),
        analyst,
        reviewer,
        max_retrievals=1,
        max_revisions=0,
        min_rerank_score=1.0,
    )
    state = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="Dataset?"))
    )
    assert state.status == "insufficient_evidence"
    assert state.evidence_pool == [evidence()]
    assert [e["evidence_id"] for e in analyst.payloads[0]["evidence"]] == [evidence().evidence_id]
    assert not reviewer.calls


async def test_research_budget_does_not_complete_a_task_with_discarded_evidence() -> None:
    tasks = ResearchPlan(
        objective="Compare",
        required_aspects=["method", "dataset"],
        subtasks=[
            SubTask(task_id="method", question="Method?", aspect="method", queries=["method"]),
            SubTask(task_id="dataset", question="Dataset?", aspect="dataset", queries=["dataset"]),
        ],
    )
    graph = build_research(
        RoundSearch([[evidence()], [dataset_evidence()]]),
        MockProvider([tasks]),
        MockProvider([]),
        MockProvider([AnalysisResult(claims=claims())]),
        MockProvider([aspect_verdict(claims(), ["dataset"])]),
        max_retrievals=1,
        max_evidence_records=1,
    )
    state = MultiAgentState.model_validate(
        await graph.ainvoke(MultiAgentState(research_question="Method and dataset?"))
    )
    assert state.status == "insufficient_evidence"
    assert state.evidence_pool == [evidence()]
    assert state.completed_tasks == ["method"]
    assert state.errors == ["evidence_budget_exhausted"]


async def test_verifier_gets_cited_quotes_without_uncited_source_text() -> None:
    cited = evidence().model_copy(update={"content": evidence().content + " Uncited extra text."})
    reviewer = CapturingProvider([verdict()])
    validation = await verify_claims(
        claims(), [cited, dataset_evidence()], ["method"], reviewer, "What method?"
    )
    assert validation.valid
    supplied = reviewer.payloads[0]["evidence"]
    assert len(supplied) == 1 and supplied[0]["evidence_id"] == cited.evidence_id
    assert supplied[0]["quote"] == cited.quote and supplied[0]["paper"] == cited.paper.model_dump()
    assert "content" not in supplied[0]


async def test_rag_sends_quote_once_and_retains_unverified_limitations() -> None:
    notes = ["Unverified limitation: the model reports an unsupported numerical result."]
    analyst = CapturingProvider([AnswerDraft(claims=claims(), limitations=notes)])
    reviewer = CapturingProvider([verdict()])
    search = Search()
    result = RAGState.model_validate(
        await build_rag(
            search,
            MockProvider(
                [
                    QueryPlan(
                        queries=["rewritten"],
                        rerank_query="model override",
                        required_aspects=["method"],
                    )
                ]
            ),
            MockProvider([]),
            analyst,
            reviewer,
        ).ainvoke(RAGState(query="What training method?"))
    )
    assert result.status == "completed"
    assert result.limitations == notes
    assert notes[0] not in result.answer
    assert search.calls[0].rerank_query == "What training method?"
    assert result.reranked_evidence[0].content == evidence().content
    supplied = analyst.payloads[0]["evidence"][0]
    assert supplied["quote"] == evidence().quote
    assert "content" not in supplied
    assert "limitations" not in reviewer.payloads[0]


async def test_research_sends_quote_once_and_reranks_for_the_subtask_question() -> None:
    analyst = CapturingProvider([AnalysisResult(claims=claims())])
    search = Search()
    result = MultiAgentState.model_validate(
        await build_research(
            search,
            MockProvider([plan()]),
            MockProvider([]),
            analyst,
            MockProvider([verdict()]),
        ).ainvoke(MultiAgentState(research_question="What training method?"))
    )
    assert result.status == "completed"
    assert search.calls[0].rerank_query == "method?"
    assert result.evidence_pool[0].content == evidence().content
    supplied = analyst.payloads[0]["evidence"][0]
    assert supplied["quote"] == evidence().quote
    assert "content" not in supplied
