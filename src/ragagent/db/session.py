from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ragagent.settings import get_settings


@lru_cache
def engine() -> Engine:
    return create_engine(get_settings().database_url.get_secret_value(), pool_pre_ping=True)


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(engine(), expire_on_commit=False)
