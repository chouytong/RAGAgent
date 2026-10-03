from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql+psycopg://ragagent:ragagent@db:5432/ragagent")
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    data_dir: Path = Path("data")
    agent_config: Path = Path("config/agents.yaml")
    embedding_backend: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = Field(default=384, ge=1, le=2000)
    embedding_api_base: str | None = None
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_backend: str = "local"
    chunk_target_tokens: int = Field(default=400, ge=32)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    candidate_top_n: int = Field(default=30, ge=1, le=200)
    evidence_top_k: int = Field(default=8, ge=1, le=50)
    rrf_k: int = Field(default=60, ge=1)
    max_retrieval_retries: int = Field(default=2, ge=0, le=5)
    max_revisions: int = Field(default=2, ge=0, le=5)
    max_iterations: int = Field(default=12, ge=1, le=50)
    provider_timeout: float = Field(default=60, gt=0, le=300)
    max_upload_bytes: int = Field(default=30 * 1024 * 1024, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
