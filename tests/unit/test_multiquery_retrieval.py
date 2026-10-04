from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper
from ragagent.domain.documents import SourceContext, SourceSpan
from ragagent.domain.research import Candidate, MetadataFilter, QueryPlan
from ragagent.retrieval.evidence import accepted_evidence
from ragagent.retrieval.service import DenseRetriever, HybridRetriever, record
from tests.unit.helpers import candidate


class StubSession:
    def get(self, model: Any, identifier: str) -> object:
        return object()

    def flush(self) -> None:
        pass


class QuerySearch:
    async def search(
        self,
        query: str,
        filters: MetadataFilter,
        top_n: int,
        author_cache: dict[str, list[str]] | None = None,
    ) -> list[Candidate]:
        return [candidate("dataset" if "dataset" in query else "method")]


class AspectReranker:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def rerank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        self.queries.append(query)
        return [
            item.model_copy(
                update={
                    "evidence": item.evidence.model_copy(
                        update={
                            "evidence_id": "00000000-0000-0000-0000-000000000002"
                            if item.evidence.chunk_id == "dataset"
                            else item.evidence.evidence_id,
                            "scores": {
                                "rerank": 2.0
                                if item.evidence.chunk_id == "method" or "dataset" in query
                                else -2.0
                            },
                        }
                    )
                }
            )
            for item in candidates[:top_k]
        ]


@pytest.mark.parametrize("original_question", [None, "Compare each method and dataset."])
async def test_secondary_aspect_survives_reranking(original_question: str | None) -> None:
    reranker = AspectReranker()
    retriever = HybridRetriever(cast(Session, StubSession()), cast(Any, object()), reranker)
    retriever.dense = cast(Any, QuerySearch())
    retriever.lexical = cast(Any, QuerySearch())
    plan = QueryPlan(
        queries=["training method", "evaluation dataset"], rerank_query=original_question
    )
    result = await retriever.search(plan)
    assert {item.chunk_id for item in accepted_evidence(result.evidence)} == {"method", "dataset"}
    assert reranker.queries == [original_question or "training method\nevaluation dataset"]


async def test_empty_filtered_corpus_does_not_resolve_embedding_revision() -> None:
    class EmptySession:
        def scalar(self, statement: Any) -> None:
            return None

    class NoModel:
        @property
        def fingerprint(self) -> str:
            raise AssertionError("empty corpus must not resolve HF metadata")

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("empty corpus must not load a model")

    result = await DenseRetriever(cast(Session, EmptySession()), NoModel()).search(
        "query", MetadataFilter(paper_ids=["absent"]), 8
    )
    assert result == []


def test_record_keeps_table_auxiliary_sources_distinct_from_original_quote() -> None:
    header = "| Method | Accuracy (%) |\n| --- | --- |\n"
    row = "| A | 99.95 |\n"
    context = SourceContext(
        source_id="#/tables/0",
        element_type="table_header",
        section_path=["Results"],
        page_start=3,
        page_end=3,
        content=header,
        quote=header,
        span_start=0,
        span_end=len(header),
    )
    source_span = SourceSpan(
        source_id="#/tables/0",
        span_start=len(header),
        span_end=len(header) + len(row),
        chunk_start=0,
        chunk_end=len(row),
    )
    chunk = Chunk(
        id="chunk",
        paper_id="paper",
        section_id="section",
        section_path="Results",
        page_start=3,
        page_end=3,
        content=row,
        metadata_json={
            "source_context": [context.model_dump()],
            "source_spans": [source_span.model_dump()],
        },
    )
    paper = Paper(
        id="paper",
        title="Paper",
        arxiv_id="2408.09869v2",
        arxiv_family_id="2408.09869",
        arxiv_version=2,
        source_status="unknown",
    )
    evidence = record(chunk, paper, 0.8, "dense", []).evidence
    assert evidence.content == evidence.quote == row
    assert evidence.source_context == [context]
    assert evidence.source_spans == [source_span]
    assert evidence.paper.arxiv_version == 2
    assert evidence.paper.source_status == "unknown"
