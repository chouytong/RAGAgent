import pytest
from sqlalchemy.orm import Session

from ragagent.domain.research import (
    AnswerDraft,
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
