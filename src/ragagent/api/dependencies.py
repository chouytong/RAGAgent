from collections.abc import Iterator

from sqlalchemy.orm import Session

from ragagent.db.session import session_factory
from ragagent.retrieval.service import HybridRetriever, SearchPort
from ragagent.runtime import make_embedder, make_reranker
from ragagent.settings import get_settings


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def get_search(session: Session) -> SearchPort:
    settings = get_settings()
    return HybridRetriever(
        session,
        make_embedder(settings),
        make_reranker(settings),
        settings.candidate_top_n,
        settings.evidence_top_k,
        settings.rrf_k,
    )
