import os
from collections.abc import Iterator

import pytest
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def redis_connection() -> Iterator[Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL required for real Redis/RQ integration")
    connection = Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
    assert connection.ping()
    yield connection
    connection.close()


@pytest.fixture
def job_sessions() -> Iterator[sessionmaker[Session]]:
    """Committed rows are visible to the real worker's separate connection."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL integration")
    engine = create_engine(url, hide_parameters=True)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()
