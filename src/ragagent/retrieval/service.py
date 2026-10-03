import uuid
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ragagent.db.models import Author, Chunk, Evidence, Paper, PaperAuthor
from ragagent.domain.research import (
    Candidate,
    EvidenceRecord,
    MetadataFilter,
    PaperMetadata,
    QueryPlan,
    SearchResult,
)
from ragagent.providers.ports import Embedder
from ragagent.retrieval.filters import apply_filters
from ragagent.retrieval.fusion import RRFusion
from ragagent.retrieval.reranker import Reranker


def record(session: Session, chunk: Chunk, paper: Paper, score: float, source: str) -> Candidate:
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chunk:{chunk.id}:0:{len(chunk.content)}"))
    authors = list(
        session.scalars(
            select(Author.name)
            .join(PaperAuthor)
            .where(PaperAuthor.paper_id == paper.id)
            .order_by(PaperAuthor.position)
        )
    )
    evidence = EvidenceRecord(
        evidence_id=eid,
        paper=PaperMetadata(
            paper_id=paper.id,
            title=paper.title,
            authors=authors,
            year=paper.year,
            venue=paper.venue,
            arxiv_id=paper.arxiv_id,
        ),
        chunk_id=chunk.id,
        section_id=chunk.section_id,
        section_path=chunk.section_path,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        content=chunk.content,
        quote=chunk.content,
        span_start=0,
        span_end=len(chunk.content),
        scores={source: score},
    )
    return Candidate(evidence=evidence, score=score)


class DenseRetriever:
    def __init__(self, session: Session, embedder: Embedder) -> None:
        self.session, self.embedder = session, embedder

    async def search(self, query: str, filters: MetadataFilter, top_n: int) -> list[Candidate]:
        vector = (await self.embedder.embed([query]))[0]
        distance = Chunk.embedding.cosine_distance(vector)
        statement = (
            select(Chunk, Paper, (1 - distance).label("score"))
            .join(Paper)
            .where(Paper.status == "indexed", Paper.embedding_model == self.embedder.fingerprint)
        )
        statement = apply_filters(statement, filters).order_by(distance, Chunk.id).limit(top_n)
        return [
            record(self.session, c, p, float(s), "dense")
            for c, p, s in self.session.execute(statement)
        ]


class LexicalRetriever:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def search(self, query: str, filters: MetadataFilter, top_n: int) -> list[Candidate]:
        tsquery = func.websearch_to_tsquery("english", query)
        score = func.ts_rank_cd(Chunk.search_vector, tsquery)
        statement = (
            select(Chunk, Paper, score)
            .join(Paper)
            .where(Paper.status == "indexed", Chunk.search_vector.op("@@")(tsquery))
        )
        statement = apply_filters(statement, filters).order_by(score.desc(), Chunk.id).limit(top_n)
        return [
            record(self.session, c, p, float(s), "lexical")
            for c, p, s in self.session.execute(statement)
        ]


class SearchPort(Protocol):
    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult: ...


class HybridRetriever:
    def __init__(
        self,
        session: Session,
        embedder: Embedder,
        reranker: Reranker,
        top_n: int = 30,
        top_k: int = 8,
        rrf_k: int = 60,
    ) -> None:
        self.session = session
        self.dense = DenseRetriever(session, embedder)
        self.lexical = LexicalRetriever(session)
        self.reranker, self.top_n, self.top_k = reranker, top_n, top_k
        self.fusion = RRFusion(rrf_k)

    async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
        dense_lists, lexical_lists = [], []
        for query in plan.queries:
            dense_lists.append(await self.dense.search(query, plan.filters, self.top_n))
            lexical_lists.append(await self.lexical.search(query, plan.filters, self.top_n))
        dense = self.fusion.fuse(dense_lists, self.top_n)
        lexical = self.fusion.fuse(lexical_lists, self.top_n)
        fused = self.fusion.fuse(dense_lists + lexical_lists, self.top_n)
        ranked = (
            await self.reranker.rerank(plan.queries[0], fused, self.top_k)
            if rerank
            else fused[: self.top_k]
        )
        evidence = [c.evidence for c in ranked]
        for e in evidence:
            existing = self.session.get(Evidence, e.evidence_id)
            if existing is None:
                self.session.add(
                    Evidence(
                        id=e.evidence_id,
                        chunk_id=e.chunk_id,
                        span_start=e.span_start,
                        span_end=e.span_end,
                        quote=e.quote,
                        scores=e.scores,
                    )
                )
        self.session.flush()
        return SearchResult(dense=dense, lexical=lexical, fused=fused, evidence=evidence)
