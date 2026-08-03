"""SQLAlchemy engine and request-scoped session dependency."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.observability.instrumentation import instrument_sqlalchemy_engine


@lru_cache
def get_engine() -> Engine:
    """Create the process-wide SQLAlchemy engine lazily."""
    settings = get_settings()
    options: dict[str, object] = {"pool_pre_ping": True}
    if make_url(settings.database_url).get_backend_name() != "sqlite":
        options.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout_seconds,
                "pool_recycle": settings.database_pool_recycle_seconds,
                "connect_args": {
                    "connect_timeout": settings.database_connect_timeout_seconds
                },
            }
        )
    engine = create_engine(settings.database_url, **options)
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


def dispose_engine() -> None:
    """Dispose pooled connections and clear cached factories during shutdown."""
    get_session_factory.cache_clear()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()
