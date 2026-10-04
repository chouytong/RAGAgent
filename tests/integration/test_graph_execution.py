import pytest
from sqlalchemy.orm import Session

from ragagent.domain.research import (
    AnswerDraft,
    Candidate,
    Claim,
    ClaimVerdict,
    QueryPlan,
    VerificationResponse,
)
from ragagent.graphs.rag import build_rag
from ragagent.graphs.state import RAGState
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.service import HybridRetriever
from tests.integration.test_retrieval import Embedder, FixtureReranker, populate


@pytest.mark.integration
async def test_graph_executes_real_database_retrieval(empty_db: Session) -> None:
    populate(empty_db)
    retrieval = HybridRetriever(empty_db, Embedder(), FixtureReranker())
    result = await retrieval.search(QueryPlan(queries=["contrastive training"]))
    eid = result.evidence[0].evidence_id
    graph = build_rag(
        retrieval,
        MockProvider([QueryPlan(queries=["contrastive training"], required_aspects=["method"])]),
        MockProvider([]),
        MockProvider(
            [
                AnswerDraft(
                    claims=[
                        Claim(
                            claim_id="c",
                            text="Contrastive training improves retrieval.",
                            evidence_ids=[eid],
                            aspect="method",
                        )
                    ]
                )
            ]
        ),
        MockProvider(
            [
                VerificationResponse(
                    question_answered=True,
                    verdicts=[ClaimVerdict(claim_id="c", supported=True, reason="exact quote")],
                )
            ]
        ),
    )
    state = RAGState.model_validate(
        await graph.ainvoke(RAGState(query="What method improves retrieval?"))
    )
    assert state.citation_validation and state.citation_validation.valid
    assert state.status == "completed" and eid in state.answer


@pytest.mark.integration
async def test_graph_cannot_cite_database_evidence_rejected_by_gate(empty_db: Session) -> None:
    populate(empty_db)

    class ThresholdReranker:
        async def rerank(
            self, query: str, candidates: list[Candidate], top_k: int
        ) -> list[Candidate]:
            return [
                candidate.model_copy(
                    update={
                        "evidence": candidate.evidence.model_copy(
                            update={
                                "scores": {
                                    **candidate.evidence.scores,
                                    "rerank": -5.0
                                    if candidate.evidence.paper.title == "Contrastive"
                                    else 1.0,
                                }
                            }
                        )
                    }
                )
                for candidate in candidates[:top_k]
            ]

    retrieval = HybridRetriever(empty_db, Embedder(), ThresholdReranker())
    retrieved = await retrieval.search(QueryPlan(queries=["contrastive training"]))
    rejected = next(e for e in retrieved.evidence if e.paper.title == "Contrastive")
    reviewer = MockProvider([])
    graph = build_rag(
        retrieval,
        MockProvider([QueryPlan(queries=["contrastive training"], required_aspects=["method"])]),
        MockProvider([]),
        MockProvider(
            [
                AnswerDraft(
                    claims=[
                        Claim(
                            claim_id="c",
                            text="Contrastive training improves retrieval.",
                            evidence_ids=[rejected.evidence_id],
                            aspect="method",
                        )
                    ]
                )
            ]
        ),
        reviewer,
        max_retries=0,
    )
    state = RAGState.model_validate(await graph.ainvoke(RAGState(query="What method?")))
    assert state.status == "insufficient_evidence" and not state.claims
    assert rejected.evidence_id not in {e.evidence_id for e in state.reranked_evidence}
    assert not reviewer.calls
