"""Authentication boundaries without DB, Redis, models or network."""

import hashlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ragagent.api import auth
from ragagent.api.app import app
from ragagent.settings import get_settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    auth._sessions.clear()
    # No context-manager lifespan: unauthorized requests must stop before services.
    with_service_free_client = TestClient(app)
    yield with_service_free_client
    with_service_free_client.close()
    auth._sessions.clear()


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/providers"),
        ("PUT", "/api/providers"),
        ("GET", "/api/conversations"),
        ("GET", "/api/queues"),
        ("POST", "/api/evaluations/retrieval"),
        ("POST", "/api/papers/upload"),
        ("GET", "/api/papers/00000000-0000-0000-0000-000000000001/pdf"),
        ("GET", "/api/runs/00000000-0000-0000-0000-000000000001/events"),
    ],
)
def test_all_private_routes_fail_before_dependencies(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error_code"] == "local_auth_required"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["www-authenticate"] == "Bearer"


def test_hash_is_not_bearer_and_missing_configuration_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_token: SecretStr
) -> None:
    token = auth_token.get_secret_value()
    verifier = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.delenv("LOCAL_AUTH_TOKEN")
    monkeypatch.setenv("LOCAL_AUTH_TOKEN_HASH", verifier)
    get_settings.cache_clear()
    assert client.get("/api/auth/status").json() == {"initialized": True, "authenticated": False}
    assert (
        client.get("/api/auth/status", headers={"Authorization": "Bearer " + verifier}).json()[
            "authenticated"
        ]
        is False
    )
    assert (
        client.get("/api/auth/status", headers={"Authorization": "Bearer " + token}).json()[
            "authenticated"
        ]
        is True
    )
    monkeypatch.delenv("LOCAL_AUTH_TOKEN_HASH")
    get_settings.cache_clear()
    assert client.get("/api/auth/status").json() == {"initialized": False, "authenticated": False}
    assert client.get("/api/health").json() == {"status": "ok"}
    assert (
        client.get("/api/conversations", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )


def test_http_only_session_wrong_bearer_duplicates_logout_and_expiry(
    client: TestClient, auth_token: SecretStr, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = {"Authorization": "Bearer " + auth_token.get_secret_value()}
    response = client.post("/api/auth/session", headers=authorization)
    assert response.status_code == 200 and response.json() == {"authenticated": True}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert client.get("/api/auth/status").json()["authenticated"] is True
    assert (
        client.get("/api/auth/status", headers={"Authorization": "Bearer " + "x" * 43}).json()[
            "authenticated"
        ]
        is False
    )
    duplicated = [("Authorization", authorization["Authorization"])] * 2
    assert client.get("/api/auth/status", headers=duplicated).json()["authenticated"] is False
    session = client.cookies.get(auth.COOKIE_NAME)
    assert (
        client.get(
            "/api/auth/status",
            headers={"Cookie": f"{auth.COOKIE_NAME}={session}; {auth.COOKIE_NAME}={session}"},
        ).json()["authenticated"]
        is False
    )
    assert all(key != session for key in auth._sessions)
    assert client.delete("/api/auth/session").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is False
    client.post("/api/auth/session", headers=authorization)
    deadline = max(value[1] for value in auth._sessions.values())
    monkeypatch.setattr(auth.time, "monotonic", lambda: deadline + 1)
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_session_rotation_origin_and_additional_web_token(
    client: TestClient, auth_token: SecretStr, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = auth_token.get_secret_value()
    native = "n" * 64
    monkeypatch.setenv("LOCAL_AUTH_TOKEN_HASH", auth.digest(native))
    get_settings.cache_clear()
    assert (
        client.get("/api/auth/status", headers={"Authorization": "Bearer " + native}).json()[
            "authenticated"
        ]
        is True
    )
    response = client.post(
        "/api/auth/session",
        headers={"Authorization": "Bearer " + token, "Origin": "http://evil.invalid"},
    )
    assert response.status_code == 403 and not auth._sessions
    assert (
        client.post("/api/auth/session", headers={"Authorization": "Bearer " + token}).status_code
        == 200
    )
    monkeypatch.delenv("LOCAL_AUTH_TOKEN")
    # Session bound to Web token cannot transfer to still-authorized native token.
    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert (
        client.get("/api/auth/status", headers={"Authorization": "Bearer " + native}).json()[
            "authenticated"
        ]
        is True
    )


def test_invalid_environment_returns_safe_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_AUTH_TOKEN", "invalid private value")
    response = client.get("/api/conversations")
    assert response.status_code == 503
    assert response.json()["error_code"] == "local_auth_configuration_invalid"
    assert "private value" not in response.text


def test_openapi_describes_bearer_auth_and_actual_public_routes() -> None:
    schema = app.openapi()
    for path in ("/api/health", "/api/ready", "/api/auth/status"):
        assert schema["paths"][path]["get"]["security"] == []
    assert schema["paths"]["/api/papers"]["get"]["security"] == [{"HTTPBearer": []}]
