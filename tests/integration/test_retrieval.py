import pytest
from sqlalchemy.orm import Session

from ragagent.db.models import Author, Chunk, ChunkEntity, Entity, Paper, PaperAuthor, Section
from ragagent.domain.research import Candidate, MetadataFilter, QueryPlan
from ragagent.retrieval.service import HybridRetriever


class Embedder:
    fingerprint = "test:384"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


class FixtureReranker:
    async def rerank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        return [
            c.model_copy(
                update={
                    "evidence": c.evidence.model_copy(
                        update={"scores": {**c.evidence.scores, "rerank": 1.0}}
                    )
                }
            )
            for c in candidates[:top_k]
        ]


def populate(db: Session) -> tuple[str, str]:
    p = Paper(
        title="Contrastive",
        sha256="b" * 64,
        original_path="fixture",
        year=2024,
        venue="ICML",
        status="indexed",
        embedding_model="test:384",
    )
    other = Paper(
        title="Other",
        sha256="c" * 64,
        original_path="fixture",
        year=2020,
        status="indexed",
        embedding_model="test:384",
    )
    db.add_all([p, other])
    db.flush()
    ids = []
    for paper, vector in [(p, [1.0] + [0.0] * 383), (other, [0.0, 1.0] + [0.0] * 382)]:
        s = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
        db.add(s)
        db.flush()
        c = Chunk(
            paper_id=paper.id,
            section_id=s.id,
            section_path="Methods",
            page_start=1,
            page_end=1,
            element_type="text",
            content="Contrastive training improves retrieval.",
            token_count=5,
            ordinal=0,
            embedding=vector,
        )
        db.add(c)
        db.flush()
        ids.append(c.id)
    a = Author(name="Alice")
    db.add(a)
    db.flush()
    db.add(PaperAuthor(paper_id=p.id, author_id=a.id, position=0))
    for kind, name in [("dataset", "SciDocs"), ("method", "Contrastive"), ("metric", "Recall")]:
        e = Entity(name=name, entity_type=kind)
        db.add(e)
        db.flush()
        db.add(ChunkEntity(chunk_id=ids[0], entity_id=e.id))
    db.flush()
    return p.id, ids[0]


@pytest.mark.integration
async def test_real_vector_fts_hybrid_and_all_filters(empty_db: Session) -> None:
    pid, cid = populate(empty_db)
    retriever = HybridRetriever(empty_db, Embedder(), FixtureReranker())
    filters = MetadataFilter(
        paper_ids=[pid],
        authors=["alice"],
        year_start=2023,
        year_end=2025,
        venues=["icml"],
        sections=["Methods"],
        entity_types=["dataset"],
        datasets=["scidocs"],
        methods=["contrastive"],
        metrics=["recall"],
    )
    result = await retriever.search(QueryPlan(queries=["contrastive training"], filters=filters))
    assert [c.evidence.chunk_id for c in result.dense] == [cid]
    assert [c.evidence.chunk_id for c in result.lexical] == [cid]
    assert result.evidence[0].chunk_id == cid
    absent = await retriever.search(
        QueryPlan(queries=["contrastive"], filters=MetadataFilter(datasets=["absent"]))
    )
    assert not absent.dense and not absent.lexical and not absent.evidence


@pytest.mark.integration
@pytest.mark.parametrize(
    "filters",
    [
        MetadataFilter(paper_ids=["absent"]),
        MetadataFilter(authors=["Nobody"]),
        MetadataFilter(year_start=2025),
        MetadataFilter(year_end=2019),
        MetadataFilter(venues=["absent"]),
        MetadataFilter(sections=["absent"]),
        MetadataFilter(entity_types=["absent"]),
        MetadataFilter(datasets=["absent"]),
        MetadataFilter(methods=["absent"]),
        MetadataFilter(metrics=["absent"]),
    ],
)
async def test_each_filter_excludes_both_retrievers(
    empty_db: Session, filters: MetadataFilter
) -> None:
    populate(empty_db)
    retriever = HybridRetriever(empty_db, Embedder(), FixtureReranker())
    result = await retriever.search(QueryPlan(queries=["contrastive"], filters=filters))
    assert not result.dense and not result.lexical


@pytest.mark.integration
async def test_parent_section_filter_includes_child_path(empty_db: Session) -> None:
    pid, cid = populate(empty_db)
    c = empty_db.get(Chunk, cid)
    assert c is not None
    parent_id = c.section_id
    child = Section(
        paper_id=pid, parent_id=parent_id, title="Training", path="Methods / Training", ordinal=1
    )
    empty_db.add(child)
    empty_db.flush()
    c.section_id, c.section_path = child.id, child.path
    empty_db.flush()
    result = await HybridRetriever(empty_db, Embedder(), FixtureReranker()).search(
        QueryPlan(
            queries=["contrastive"], filters=MetadataFilter(paper_ids=[pid], sections=["methods"])
        )
    )
    assert result.evidence and result.evidence[0].chunk_id == cid


@pytest.mark.integration
async def test_empty_corpus_does_not_call_model(empty_db: Session) -> None:
    class NoModel:
        fingerprint = "test:384"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("empty corpus must not call embedding provider")

    result = await HybridRetriever(empty_db, NoModel(), FixtureReranker()).search(
        QueryPlan(queries=["q"])
    )
    assert not result.dense and not result.lexical and not result.evidence
