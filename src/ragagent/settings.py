from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    database_url: SecretStr = SecretStr("postgresql+psycopg://ragagent:ragagent@db:5432/ragagent")
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    data_dir: Path = Path("data")
    agent_config: Path = Path("config/agents.yaml")
    embedding_backend: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = Field(default=384, ge=1, le=2000)
    embedding_api_base: str | None = None
    embedding_revision: str | None = None
    embedding_api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_backend: str = "local"
    reranker_revision: str | None = None
    chunk_target_tokens: int = Field(default=400, ge=32)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    candidate_top_n: int = Field(default=30, ge=1, le=200)
    evidence_top_k: int = Field(default=8, ge=1, le=50)
    minimum_rerank_score: float = Field(default=0.0, allow_inf_nan=False)
    rrf_k: int = Field(default=60, ge=1)
    max_retrieval_retries: int = Field(default=2, ge=0, le=5)
    max_revisions: int = Field(default=2, ge=0, le=5)
    max_iterations: int = Field(default=12, ge=1, le=50)
    rag_evidence_budget: int = Field(default=48, ge=1, le=256)
    research_evidence_budget: int = Field(default=96, ge=1, le=512)
    provider_timeout: float = Field(default=60, gt=0, le=300)
    evaluation_timeout_seconds: int = Field(default=7200, ge=1800, le=86400)
    conversation_recent_message_limit: int = Field(default=8, ge=2, le=64)
    conversation_context_token_budget: int = Field(default=8192, ge=4096, le=65536)
    conversation_summary_max_bytes: int = Field(default=2048, ge=256, le=16384)
    conversation_message_max_bytes: int = Field(default=2048, ge=256, le=16384)
    max_upload_bytes: int = Field(default=30 * 1024 * 1024, ge=1)

    @model_validator(mode="after")
    def valid_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be less than chunk_target_tokens")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
