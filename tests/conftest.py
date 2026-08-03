"""Shared pytest fixtures."""

from collections.abc import Iterator
from os import environ

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
environ.setdefault("KAFKA_ENABLED", "false")

from app.db.base import Base
from app.db.session import get_db
from app.events.dependencies import get_outbox_dispatcher
from app.events.dispatcher import DispatchOutcome
from app.main import app


class DeferredTestDispatcher:
    """Keep API tests broker-independent while preserving durable outbox rows."""

    def dispatch_event(self, _event_id: str) -> DispatchOutcome:
        return DispatchOutcome.DEFERRED


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Create an isolated in-memory SQLite database for each test."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    """Provide an API client using the isolated test database."""

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_outbox_dispatcher] = DeferredTestDispatcher
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
