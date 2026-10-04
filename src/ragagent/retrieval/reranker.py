import asyncio
import threading
from typing import Any, Protocol

from ragagent.domain.documents import contextual_text
from ragagent.domain.research import Candidate
from ragagent.errors import ProviderError
from ragagent.providers.model_identity import LocalModelIdentity


class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[Candidate], top_k: int
    ) -> list[Candidate]: ...


class CrossEncoderReranker:
    def __init__(self, model: str, revision: str | None = None) -> None:
        self.model_name = model
        self.identity = LocalModelIdentity(model, revision)
        self._model: Any = None
        self._load_lock = threading.Lock()

    @property
    def revision(self) -> str:
        return self.identity.revision

    def _rank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        from sentence_transformers import CrossEncoder

        revision = self.identity.sdk_revision
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    self._model = (
                        CrossEncoder(self.model_name)
                        if revision is None
                        else CrossEncoder(self.model_name, revision=revision)
                    )
        scores = self._model.predict(
            [
                (query, contextual_text(c.evidence.content, c.evidence.source_context))
                for c in candidates
            ]
        ).tolist()
        self.identity.verify_directory()
        ranked = [
            Candidate(
                evidence=c.evidence.model_copy(
                    update={"scores": {**c.evidence.scores, "rerank": float(score)}}
                ),
                score=float(score),
            )
            for c, score in zip(candidates, scores, strict=True)
        ]
        return sorted(ranked, key=lambda c: (-c.score, c.evidence.chunk_id))[:top_k]

    async def rerank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        if not candidates:
            return []
        try:
            return await asyncio.to_thread(self._rank, query, candidates, top_k)
        except Exception:
            raise ProviderError("reranking_failed") from None
