import asyncio
import hashlib
import json
import math
import sys
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ragagent.errors import ConfigurationError, ProviderError
from ragagent.providers.chat import Usage
from ragagent.providers.environment import runtime_value
from ragagent.providers.model_identity import LocalModelIdentity

SUPPORTED_EMBEDDING_PROVIDERS = frozenset({"openai", "cohere", "cohere_chat", "voyage"})


class LocalEmbedder:
    def __init__(self, model: str, dimension: int, revision: str | None = None) -> None:
        self.model_name = model
        self.dimension = dimension
        self.identity = LocalModelIdentity(model, revision)
        self.usage = Usage()
        self._model: Any = None
        self._load_lock = threading.Lock()

    @property
    def revision(self) -> str:
        return self.identity.revision

    @property
    def fingerprint(self) -> str:
        revision = self.revision
        self.identity.verify_directory()
        identity = json.dumps(
            {
                "backend": "local",
                "model": self.model_name,
                "dimension": self.dimension,
                "revision": revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "local:v2:" + hashlib.sha256(identity.encode()).hexdigest()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        from sentence_transformers import SentenceTransformer

        revision = self.identity.sdk_revision
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    self._model = (
                        SentenceTransformer(self.model_name)
                        if revision is None
                        else SentenceTransformer(self.model_name, revision=revision)
                    )
        vectors: list[list[float]] = self._model.encode(texts, normalize_embeddings=True).tolist()
        self.identity.verify_directory()
        validate_vectors(vectors, len(texts), self.dimension)
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = await asyncio.to_thread(self._encode, texts)
            self.usage.record_cost(0.0)
            return vectors
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
        self.usage = Usage()

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
            self.usage.begin_call()
            try:
                response = await litellm.aembedding(
                    model=self.model, input=texts, api_base=self.api_base, api_key=key, timeout=60
                )
            except BaseException:
                # A timeout/cancellation can occur after the provider accepted a billed request.
                self.usage.record_cost(None)
                raise
            try:
                cost = float(litellm.completion_cost(completion_response=response))
            except Exception:
                cost = None
            self.usage.record_identity(
                getattr(response, "model", None), getattr(response, "system_fingerprint", None)
            )
            response_usage = getattr(response, "usage", None)
            tokens = (
                response_usage.get("prompt_tokens", 0)
                if isinstance(response_usage, dict)
                else getattr(response_usage, "prompt_tokens", 0)
            )
            if isinstance(tokens, int) and tokens >= 0:
                self.usage.prompt_tokens += tokens
            self.usage.record_cost(cost)
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
