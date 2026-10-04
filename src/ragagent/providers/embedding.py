import asyncio
import hashlib
import json
import math
import sys
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ragagent.errors import ConfigurationError, ProviderError
from ragagent.providers.environment import runtime_value

SUPPORTED_EMBEDDING_PROVIDERS = frozenset({"openai", "cohere", "cohere_chat", "voyage"})


class LocalEmbedder:
    def __init__(self, model: str, dimension: int, revision: str | None = None) -> None:
        self.model_name = model
        self.dimension = dimension
        self.revision = revision
        self._model: Any = None
        self._load_lock = threading.Lock()

    @property
    def fingerprint(self) -> str:
        original = f"local:{self.model_name}:{self.dimension}"
        return original if self.revision is None else f"{original}:revision:{self.revision}"

    def _encode(self, texts: list[str]) -> list[list[float]]:
        from sentence_transformers import SentenceTransformer

        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    self._model = (
                        SentenceTransformer(self.model_name)
                        if self.revision is None
                        else SentenceTransformer(self.model_name, revision=self.revision)
                    )
        vectors: list[list[float]] = self._model.encode(texts, normalize_embeddings=True).tolist()
        validate_vectors(vectors, len(texts), self.dimension)
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await asyncio.to_thread(self._encode, texts)
        except Exception:
            raise ProviderError("local_embedding_failed") from None


class LiteLLMEmbedder:
    def __init__(
        self,
        model: str,
        dimension: int,
        api_base: str | None = None,
        revision: str | None = None,
        api_key_env: str | None = None,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self.api_base = embedding_endpoint(model, api_base)
        self.revision = revision
        self.api_key_env = api_key_env

    @property
    def fingerprint(self) -> str:
        identity = json.dumps(
            {
                "backend": "litellm",
                "model": self.model,
                "dimension": self.dimension,
                "endpoint": self.api_base,
                "revision": self.revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "litellm:v2:" + hashlib.sha256(identity.encode()).hexdigest()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        try:
            key_env = self.api_key_env or {
                "openai": "OPENAI_API_KEY",
                "cohere": "COHERE_API_KEY",
                "cohere_chat": "COHERE_API_KEY",
                "voyage": "VOYAGE_API_KEY",
            }.get(self.model.partition("/")[0])
            key = runtime_value(key_env) if key_env else None
            if self.api_key_env and not key:
                raise ConfigurationError("embedding_key_missing")
            response = await litellm.aembedding(
                model=self.model, input=texts, api_base=self.api_base, api_key=key, timeout=60
            )
            vectors = [row["embedding"] for row in sorted(response.data, key=lambda r: r["index"])]
            validate_vectors(vectors, len(texts), self.dimension)
            return vectors
        except ConfigurationError:
            raise
        except Exception:
            raise ProviderError("embedding_failed") from None


def embedding_endpoint(model: str, api_base: str | None) -> str:
    """Freeze a supported provider's endpoint for both indexing identity and transport."""
    provider = model.partition("/")[0]
    if provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise ConfigurationError("unsupported_embedding_provider")
    if api_base is not None:
        endpoint = normalize_endpoint(api_base)
    elif provider != "openai":
        raise ConfigurationError("embedding_api_base_required")
    else:
        # Match the pinned SDK's OpenAI embedding precedence without importing it
        # during empty-index startup. An unloaded SDK has no configured global base.
        sdk_base = getattr(sys.modules.get("litellm"), "api_base", None)
        if sdk_base is not None and not isinstance(sdk_base, str):
            raise ConfigurationError("invalid_embedding_api_base")
        endpoint = normalize_endpoint(
            sdk_base
            or runtime_value("OPENAI_BASE_URL")
            or runtime_value("OPENAI_API_BASE")
            or "https://api.openai.com/v1"
        )
    assert endpoint is not None
    return endpoint


def normalize_endpoint(endpoint: str | None) -> str | None:
    if endpoint is None:
        return None
    try:
        url = urlsplit(endpoint.strip())
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError
        host = url.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = url.port
        if port is not None and (url.scheme, port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        return urlunsplit((url.scheme, host, url.path.rstrip("/"), "", ""))
    except ValueError:
        raise ConfigurationError("invalid_embedding_api_base") from None


def validate_vectors(vectors: list[list[float]], count: int, dimension: int) -> None:
    if len(vectors) != count or any(
        len(v) != dimension or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors
    ):
        raise ValueError("invalid_embedding_shape_or_values")
