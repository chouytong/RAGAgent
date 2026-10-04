import uuid
from typing import Protocol

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from ragagent.db.models import Author, Chunk, Evidence, Paper, PaperAuthor
from ragagent.domain.documents import SourceContext, SourceSpan
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


def record(chunk: Chunk, paper: Paper, score: float, source: str, authors: list[str]) -> Candidate:
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chunk:{chunk.id}:0:{len(chunk.content)}"))
    evidence = EvidenceRecord(
        evidence_id=eid,
        paper=PaperMetadata(
            paper_id=paper.id,
            title=paper.title,
            authors=authors,
            year=paper.year,
            venue=paper.venue,
            arxiv_id=paper.arxiv_id,
            arxiv_family_id=paper.arxiv_family_id,
            arxiv_version=paper.arxiv_version,
            source_status=paper.source_status or "unknown",
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
        source_context=[
            SourceContext.model_validate(context)
            for context in (chunk.metadata_json or {}).get("source_context", [])
        ],
        source_spans=[
            SourceSpan.model_validate(span)
            for span in (chunk.metadata_json or {}).get("source_spans", [])
        ],
    )
    return Candidate(evidence=evidence, score=score)


def records(
    session: Session,
    rows: list[tuple[Chunk, Paper, float]],
    source: str,
    author_cache: dict[str, list[str]] | None = None,
) -> list[Candidate]:
    cache = {} if author_cache is None else author_cache
    missing = {paper.id for _, paper, _ in rows} - cache.keys()
    if missing:
        for paper_id in missing:
            cache[paper_id] = []
        authors = session.execute(
            select(PaperAuthor.paper_id, Author.name)
            .join(Author)
            .where(PaperAuthor.paper_id.in_(missing))
            .order_by(PaperAuthor.paper_id, PaperAuthor.position)
        )
        for paper_id, name in authors:
            cache[paper_id].append(name)
    return [record(chunk, paper, score, source, cache[paper.id]) for chunk, paper, score in rows]


class DenseRetriever:
    def __init__(self, session: Session, embedder: Embedder) -> None:
        self.session, self.embedder = session, embedder

    async def search(
        self,
        query: str,
        filters: MetadataFilter,
        top_n: int,
        author_cache: dict[str, list[str]] | None = None,
    ) -> list[Candidate]:
        # Apply metadata restrictions before loading or calling an embedding model.
        probe = (
            select(Chunk, Paper, literal(0.0))
            .join(Paper)
            .where(
                Paper.status == "indexed", Paper.source_status.not_in(["withdrawn", "retracted"])
            )
        )
        probe = apply_filters(probe, filters)
        if self.session.scalar(probe.with_only_columns(Chunk.id).limit(1)) is None:
            return []
        # A default local model may resolve immutable HF metadata for its identity.
        # Do not do that, or load model weights, for an empty filtered corpus.
        fingerprint = self.embedder.fingerprint
        compatible = probe.where(Paper.embedding_model == fingerprint)
        if self.session.scalar(compatible.with_only_columns(Chunk.id).limit(1)) is None:
            return []
        vector = (await self.embedder.embed([query]))[0]
        distance = Chunk.embedding.cosine_distance(vector)
        statement = (
            select(Chunk, Paper, (1 - distance).label("score"))
            .join(Paper)
            .where(
                Paper.status == "indexed",
                Paper.embedding_model == fingerprint,
                Paper.source_status.not_in(["withdrawn", "retracted"]),
            )
        )
        statement = apply_filters(statement, filters).order_by(distance, Chunk.id).limit(top_n)
        rows = [(c, p, float(s)) for c, p, s in self.session.execute(statement)]
        return records(self.session, rows, "dense", author_cache)


class LexicalRetriever:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def search(
        self,
        query: str,
        filters: MetadataFilter,
        top_n: int,
        author_cache: dict[str, list[str]] | None = None,
    ) -> list[Candidate]:
        tsquery = func.websearch_to_tsquery("english", query)
        score = func.ts_rank_cd(Chunk.search_vector, tsquery)
        statement = (
            select(Chunk, Paper, score)
            .join(Paper)
            .where(
                Paper.status == "indexed",
                Paper.source_status.not_in(["withdrawn", "retracted"]),
                Chunk.search_vector.op("@@")(tsquery),
            )
        )
        statement = apply_filters(statement, filters).order_by(score.desc(), Chunk.id).limit(top_n)
        rows = [(c, p, float(s)) for c, p, s in self.session.execute(statement)]
        return records(self.session, rows, "lexical", author_cache)


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
        author_cache: dict[str, list[str]] = {}
        for query in plan.queries:
            dense_lists.append(
                await self.dense.search(query, plan.filters, self.top_n, author_cache)
            )
            lexical_lists.append(
                await self.lexical.search(query, plan.filters, self.top_n, author_cache)
            )
        dense = self.fusion.fuse(dense_lists, self.top_n)
        lexical = self.fusion.fuse(lexical_lists, self.top_n)
        fused = self.fusion.fuse(dense_lists + lexical_lists, self.top_n)
        ranked = (
            await self.reranker.rerank(
                plan.rerank_query or "\n".join(plan.queries), fused, self.top_k
            )
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
