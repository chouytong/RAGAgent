import os
import secrets
from collections.abc import Iterator

import pytest
from httpx import Headers
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Iterator[Session]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL integration")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
    engine.dispose()


@pytest.fixture
def empty_db(db: Session) -> Session:
    db.execute(text("TRUNCATE papers, entities, runs, conversations CASCADE"))
    return db


@pytest.fixture(scope="session")
def auth_token() -> SecretStr:
    return SecretStr(secrets.token_urlsafe(32))


@pytest.fixture(autouse=True)
def local_auth_environment(
    monkeypatch: pytest.MonkeyPatch, auth_token: SecretStr
) -> Iterator[None]:
    from ragagent.settings import get_settings

    monkeypatch.setenv("LOCAL_AUTH_TOKEN", auth_token.get_secret_value())
    monkeypatch.delenv("LOCAL_AUTH_TOKEN_HASH", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_headers(auth_token: SecretStr) -> Headers:
    # HTTPX redacts Authorization in its repr, including failing-test tracebacks.
    return Headers({"Authorization": "Bearer " + auth_token.get_secret_value()})
