"""Real PostgreSQL retrieval with scripted, unpaid comparison providers."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk
from ragagent.domain.research import AnswerDraft, MetadataFilter, QueryPlan, VerificationResponse
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import AnalysisResult, MultiAgentState, RAGState
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.service import HybridRetriever
from tests.integration.test_retrieval import Embedder, FixtureReranker, populate
from tests.unit.test_comparison_evidence import (
    ENTITIES,
    QUESTION,
    comparison_claim,
    comparison_response,
    research_plan,
)


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
@pytest.mark.parametrize("layout", ["single_chunk", "single_paper", "multi_paper"])
async def test_database_comparison_coverage_does_not_require_two_papers(
    empty_db: Session, mode: str, layout: str
) -> None:
    pid, cid = populate(empty_db)
    first = empty_db.get(Chunk, cid)
    assert first is not None
    first.content = "Method A achieved accuracy 90%."
    paper_ids = [pid]
    if layout == "single_chunk":
        first.content += " Method B achieved accuracy 85%."
    elif layout == "single_paper":
        empty_db.add(
            Chunk(
                paper_id=pid,
                section_id=first.section_id,
                section_path=first.section_path,
                page_start=first.page_start,
                page_end=first.page_end,
                element_type="text",
                content="Method B achieved accuracy 85%.",
                token_count=6,
                ordinal=1,
                embedding=[1.0] + [0.0] * 383,
            )
        )
    else:
        second = empty_db.scalar(select(Chunk).where(Chunk.paper_id != pid))
        assert second is not None
        second.content = "Method B achieved accuracy 85%."
        paper_ids.append(second.paper_id)
    empty_db.flush()
    filters = MetadataFilter(paper_ids=paper_ids)
    search = HybridRetriever(empty_db, Embedder(), FixtureReranker())
    retrieval_query = "method accuracy"
    result = await search.search(QueryPlan(queries=[retrieval_query], filters=filters))
    assert result.dense and result.lexical
    items = sorted(result.evidence, key=lambda item: "Method A" not in item.quote)
    assert len(items) == (1 if layout == "single_chunk" else 2)
    claim = comparison_claim(items)
    reviewer = MockProvider([comparison_response(items)])
    if mode == "rag":
        state = RAGState.model_validate(
            await build_rag(
                search,
                MockProvider(
                    [
                        QueryPlan(
                            queries=[retrieval_query],
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
            ).ainvoke(RAGState(query=QUESTION, filters=filters))
        )
        assert state.status == "completed" and state.citation_validation is not None
        assert state.sufficiency is not None
        assert state.sufficiency.paper_count == len(paper_ids)
        validation, output = state.citation_validation, state.answer
    else:
        plan = research_plan()
        plan.subtasks[0].queries = [retrieval_query]
        research = MultiAgentState.model_validate(
            await build_research(
                search,
                MockProvider([plan]),
                MockProvider([]),
                MockProvider([AnalysisResult(claims=[claim])]),
                reviewer,
                max_retrievals=1,
            ).ainvoke(MultiAgentState(research_question=QUESTION, filters=filters))
        )
        assert research.status == "completed" and research.review_result is not None
        validation, output = research.review_result.validation, research.draft_report
    assert validation.valid and {item.entity for item in validation.comparison_entities} == set(
        ENTITIES
    )
    assert validation.supported_pairs == comparison_response(items).supported_pairs
    assert reviewer.calls == [VerificationResponse]
    assert claim.text in output
    assert all(f"[E:{item.evidence_id}]" in output for item in items)
