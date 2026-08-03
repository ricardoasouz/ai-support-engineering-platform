"""SQLAlchemy engine and request-scoped session dependency."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.observability.instrumentation import instrument_sqlalchemy_engine


@lru_cache
def get_engine() -> Engine:
    """Create the process-wide SQLAlchemy engine lazily."""
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    instrument_sqlalchemy_engine(engine)
    return engine


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Create the process-wide session factory lazily."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Yield one database session for a request and always close it."""
    with get_session_factory()() as session:
        yield session
