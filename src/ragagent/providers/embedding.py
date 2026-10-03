import asyncio
import math
from typing import Any

from ragagent.errors import ProviderError


class LocalEmbedder:
    def __init__(self, model: str, dimension: int) -> None:
        self.model_name = model
        self.dimension = dimension
        self._model: Any = None

    @property
    def fingerprint(self) -> str:
        return f"local:{self.model_name}:{self.dimension}"

    def _encode(self, texts: list[str]) -> list[list[float]]:
        from sentence_transformers import SentenceTransformer

        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        vectors: list[list[float]] = self._model.encode(texts, normalize_embeddings=True).tolist()
        validate_vectors(vectors, len(texts), self.dimension)
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await asyncio.to_thread(self._encode, texts)
        except Exception:
            raise ProviderError("local_embedding_failed") from None


class LiteLLMEmbedder:
    def __init__(self, model: str, dimension: int, api_base: str | None = None) -> None:
        self.model = model
        self.dimension = dimension
        self.api_base = api_base

    @property
    def fingerprint(self) -> str:
        return f"litellm:{self.model}:{self.dimension}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        try:
            response = await litellm.aembedding(
                model=self.model, input=texts, api_base=self.api_base, timeout=60
            )
            vectors = [row["embedding"] for row in sorted(response.data, key=lambda r: r["index"])]
            validate_vectors(vectors, len(texts), self.dimension)
            return vectors
        except Exception:
            raise ProviderError("embedding_failed") from None


def validate_vectors(vectors: list[list[float]], count: int, dimension: int) -> None:
    if len(vectors) != count or any(
        len(v) != dimension or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors
    ):
        raise ValueError("invalid_embedding_shape_or_values")
