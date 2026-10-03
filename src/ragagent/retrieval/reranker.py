import asyncio
from typing import Any, Protocol

from ragagent.domain.research import Candidate
from ragagent.errors import ProviderError


class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[Candidate], top_k: int
    ) -> list[Candidate]: ...


class CrossEncoderReranker:
    def __init__(self, model: str) -> None:
        self.model_name = model
        self._model: Any = None

    def _rank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        from sentence_transformers import CrossEncoder

        if self._model is None:
            self._model = CrossEncoder(self.model_name)
        scores = self._model.predict([(query, c.evidence.content) for c in candidates]).tolist()
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
