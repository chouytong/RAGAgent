import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Headers

from ragagent.api.app import app
from ragagent.settings import Settings

ROLES = ("supervisor", "retriever", "analyst", "reviewer")
FIXTURE_SECRET = "fixture-provider-secret-not-a-real-key"


def mapping() -> dict[str, Any]:
    return {"agents": {role: {"provider": "openai", "model": "fixture-model"} for role in ROLES}}


@pytest.fixture
async def provider_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth_headers: Headers
) -> AsyncIterator[tuple[AsyncClient, Path]]:
    path = tmp_path / "agents.yaml"
    path.write_text(json.dumps(mapping()))
    settings = Settings(_env_file=None, agent_config=path)
    monkeypatch.setenv("OPENAI_API_KEY", FIXTURE_SECRET)
    monkeypatch.setattr("ragagent.api.providers.get_settings", lambda: settings)

    async def inline_sync_endpoint(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        # Exercise real ASGI routing/validation without a thread portal or
        # network sockets. These routes only use the temporary config file.
        return func(*args, **kwargs)

    monkeypatch.setattr("fastapi.routing.run_in_threadpool", inline_sync_endpoint)
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://testserver", headers=auth_headers
    ) as client:
        yield client, path


@pytest.mark.parametrize("field", ["api_base", "model"])
@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "DATABASE_URL"])
async def test_api_rejects_secret_templates_before_persistence(
    provider_client: tuple[AsyncClient, Path], field: str, variable: str
) -> None:
    client, path = provider_client
    original = path.read_text()
    request = mapping()
    request["agents"]["supervisor"][field] = (
        f"https://example.invalid/${{{variable}}}" if field == "api_base" else f"${{{variable}}}"
    )
    response = await client.put("/api/providers", json=request)
    assert response.status_code == 422
    assert FIXTURE_SECRET not in response.text
    assert path.read_text() == original
    response = await client.get("/api/providers")
    assert response.status_code == 200
    assert FIXTURE_SECRET not in response.text
    assert response.json()["agents"]["supervisor"]["model"] == "fixture-model"


async def test_existing_endpoint_template_fails_without_returning_or_reading_secret(
    provider_client: tuple[AsyncClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path = provider_client
    configuration = mapping()
    configuration["agents"]["supervisor"]["api_base"] = "https://example.invalid/${OPENAI_API_KEY}"
    path.write_text(json.dumps(configuration))

    reads: list[str] = []

    def unexpected_environment_read(name: str) -> str:
        reads.append(name)
        return FIXTURE_SECRET

    monkeypatch.setattr("ragagent.providers.config.runtime_value", unexpected_environment_read)
    response = await client.get("/api/providers")
    assert response.status_code == 503
    assert response.json()["error_code"] == "invalid_provider_config"
    assert FIXTURE_SECRET not in response.text
    assert reads == []


async def test_provider_mapping_round_trip_never_exports_key(
    provider_client: tuple[AsyncClient, Path],
) -> None:
    client, path = provider_client
    request = mapping()
    request["agents"]["supervisor"].update(
        model="saved-model", api_base="https://example.invalid/v1", api_key_env="OPENAI_API_KEY"
    )
    assert (await client.put("/api/providers", json=request)).status_code == 200
    response = await client.get("/api/providers")
    assert response.status_code == 200
    supervisor = response.json()["agents"]["supervisor"]
    assert supervisor["model"] == "saved-model"
    assert supervisor["api_base"] == "https://example.invalid/v1"
    assert supervisor["key_configured"] is True
    assert FIXTURE_SECRET not in response.text
    assert FIXTURE_SECRET not in path.read_text()


@pytest.mark.parametrize(
    "host", ["attacker.invalid", "localhost.attacker.invalid", "localhost:bad"]
)
async def test_foreign_or_malformed_hosts_are_rejected(
    provider_client: tuple[AsyncClient, Path], host: str
) -> None:
    client, path = provider_client
    original = path.read_text()
    response = await client.put("/api/providers", json=mapping(), headers={"Host": host})
    assert response.status_code == 403
    assert response.json()["error_code"] == "untrusted_host"
    assert path.read_text() == original
    assert (await client.get("/api/providers", headers={"Host": host})).status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.invalid",
        "http://localhost.attacker.invalid",
        "null",
        "http://testserver:8080",
        "https://testserver",
        "http://testserver/",
        "http://user@testserver",
    ],
)
async def test_cross_origin_writes_are_rejected(
    provider_client: tuple[AsyncClient, Path], origin: str
) -> None:
    client, path = provider_client
    original = path.read_text()
    response = await client.put("/api/providers", json=mapping(), headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error_code"] == "untrusted_origin"
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("testserver", "http://testserver"),
        ("testserver", "http://testserver:80"),
        ("localhost:8080", "http://localhost:8080"),
        ("127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("[::1]:8080", "http://[::1]:8080"),
    ],
)
async def test_same_origin_and_originless_cli_writes_work(
    provider_client: tuple[AsyncClient, Path], host: str, origin: str
) -> None:
    client, _ = provider_client
    assert (
        await client.put("/api/providers", json=mapping(), headers={"Host": host, "Origin": origin})
    ).status_code == 200
    assert (
        await client.put("/api/providers", json=mapping(), headers={"Host": host})
    ).status_code == 200


async def test_duplicate_origin_headers_are_rejected(
    provider_client: tuple[AsyncClient, Path],
) -> None:
    client, _ = provider_client
    response = await client.put(
        "/api/providers",
        json=mapping(),
        headers=[("Origin", "http://testserver"), ("Origin", "http://attacker.invalid")],
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "untrusted_origin"
