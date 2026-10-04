import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from ragagent.api import app as api
from ragagent.api import providers
from ragagent.api.dependencies import get_db
from ragagent.domain.research import Candidate, QueryPlan, SearchResult
from ragagent.errors import ProviderError
from ragagent.providers.chat import Usage
from ragagent.settings import Settings
from tests.unit.helpers import evidence

T = TypeVar("T", bound=BaseModel)
PRIVATE_EXCEPTION_TEXT = "fixture-private-paper-text fixture-sensitive-key"


class RecordingSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError(PRIVATE_EXCEPTION_TEXT)

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
async def accounting_client() -> AsyncIterator[tuple[AsyncClient, RecordingSession]]:
    session = RecordingSession()

    async def db_override() -> AsyncIterator[RecordingSession]:
        # Keep routing real without a sync dependency thread or a database.
        yield session

    overrides = dict(api.app.dependency_overrides)
    api.app.dependency_overrides[get_db] = db_override
    try:
        async with AsyncClient(
            transport=ASGITransport(api.app), base_url="http://testserver"
        ) as client:
            yield client, session
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(overrides)


class ChargedSearch:
    def __init__(
        self, *, previous_unknown: bool, current_unknown: bool, failure: str | None = None
    ) -> None:
        self.usage = Usage()
        self.usage.prompt_tokens = 37
        self.usage.record_cost(0.50)
        self.usage.record_cost(None if previous_unknown else 0.75)
        self.dense = SimpleNamespace(embedder=SimpleNamespace(usage=self.usage))
        self.current_unknown, self.failure = current_unknown, failure

    async def search(self, plan: QueryPlan) -> SearchResult:
        self.usage.begin_call()
        self.usage.prompt_tokens += 7
        self.usage.record_identity("fixture-embedding-version", "fixture-embedding-fingerprint")
        self.usage.record_cost(None if self.current_unknown else 0.012)
        if self.failure == "application":
            raise ProviderError("fixture_retrieval_failed")
        if self.failure == "unexpected":
            raise RuntimeError(PRIVATE_EXCEPTION_TEXT)
        item = evidence()
        candidate = Candidate(evidence=item, score=1.0)
        return SearchResult(dense=[candidate], lexical=[], fused=[candidate], evidence=[item])


def assert_current_embedding_usage(payload: dict[str, Any], *, unknown: bool) -> None:
    assert payload["usage_scope"] == "current_request"
    usage = payload["usage"]["embedding"]
    assert usage["calls"] == 1
    assert usage["in_flight_calls"] == 0
    assert usage["prompt_tokens"] == 7 and usage["completion_tokens"] == 0
    assert usage["unknown_cost_calls"] == int(unknown)
    assert usage["known_cost"] == pytest.approx(0.0 if unknown else 0.012)
    if unknown:
        assert usage["cost"] is None
    else:
        assert usage["cost"] == pytest.approx(0.012)
    assert usage["provider_models"] == ["fixture-embedding-version"]


@pytest.mark.parametrize("previous_unknown", [False, True])
@pytest.mark.parametrize("current_unknown", [False, True])
async def test_search_success_reports_only_current_request_embedding_usage(
    accounting_client: tuple[AsyncClient, RecordingSession],
    monkeypatch: pytest.MonkeyPatch,
    previous_unknown: bool,
    current_unknown: bool,
) -> None:
    client, session = accounting_client
    search = ChargedSearch(previous_unknown=previous_unknown, current_unknown=current_unknown)
    monkeypatch.setattr(api, "get_search", lambda db: search)
    response = await client.post("/api/search", json={"query": "contrastive training"})
    assert response.status_code == 200
    payload = response.json()
    assert_current_embedding_usage(payload, unknown=current_unknown)
    assert payload["evidence"][0]["evidence_id"] == evidence().evidence_id
    assert len(payload["dense"]) == len(payload["fused"]) == 1
    assert payload["lexical"] == []
    assert session.commits == 1 and session.rollbacks == 0


@pytest.mark.parametrize(
    ("failure", "status", "error_code", "unknown"),
    [
        ("application", 503, "fixture_retrieval_failed", False),
        ("unexpected", 500, "internal_error", True),
        ("commit", 500, "internal_error", False),
    ],
)
async def test_search_failure_retains_request_usage_and_rolls_back_without_raw_error(
    accounting_client: tuple[AsyncClient, RecordingSession],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    status: int,
    error_code: str,
    unknown: bool,
) -> None:
    client, session = accounting_client
    search = ChargedSearch(previous_unknown=True, current_unknown=unknown, failure=failure)
    session.fail_commit = failure == "commit"
    monkeypatch.setattr(api, "get_search", lambda db: search)
    response = await client.post("/api/search", json={"query": "contrastive training"})
    assert response.status_code == status
    payload = response.json()
    assert payload["error_code"] == error_code
    assert_current_embedding_usage(payload, unknown=unknown)
    assert PRIVATE_EXCEPTION_TEXT not in response.text
    assert "fixture-sensitive-key" not in response.text
    assert session.rollbacks == 1
    assert session.commits == int(failure == "commit")


@pytest.mark.parametrize("failure", [False, True])
async def test_provider_connectivity_success_and_failure_include_chat_usage(
    accounting_client: tuple[AsyncClient, RecordingSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: bool,
) -> None:
    client, _ = accounting_client
    path = tmp_path / "agents.yaml"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    role: {"provider": "openai", "model": "fixture-pinned-model"}
                    for role in ("supervisor", "retriever", "analyst", "reviewer")
                }
            }
        )
    )
    monkeypatch.setattr(
        providers, "get_settings", lambda: Settings(_env_file=None, agent_config=path)
    )

    class ChargedProvider:
        def __init__(self, model: Any, timeout: float) -> None:
            self.usage = Usage()

        async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
            self.usage.begin_call()
            self.usage.prompt_tokens += 9
            self.usage.record_cost(None if failure else 0.004)
            if failure:
                raise ProviderError("fixture_connectivity_failed")
            return schema.model_validate({"ok": True})

    monkeypatch.setattr(providers, "LiteLLMProvider", ChargedProvider)
    response = await client.post("/api/providers/test", json={"agent": "reviewer"})
    assert response.status_code == (503 if failure else 200)
    payload = response.json()
    assert payload["usage_scope"] == "current_request"
    usage = payload["usage"]["chat"]
    assert usage["calls"] == 1 and usage["in_flight_calls"] == 0
    assert usage["prompt_tokens"] == 9
    if failure:
        assert payload["error_code"] == "fixture_connectivity_failed"
        assert usage["cost"] is None and usage["unknown_cost_calls"] == 1
    else:
        assert payload["ok"] is True and payload["agent"] == "reviewer"
        assert payload["model"] == "fixture-pinned-model"
        assert usage["cost"] == pytest.approx(0.004)
    assert PRIVATE_EXCEPTION_TEXT not in response.text
