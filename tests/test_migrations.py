"""Alembic migration smoke tests."""

from os import environ
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


def test_initial_migration_creates_incident_schema(tmp_path: Path) -> None:
    """Apply the migration to a temporary database and inspect its schema."""
    previous_database_url = environ["DATABASE_URL"]
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        engine = create_engine(database_url)
        inspector = inspect(engine)

        assert set(inspector.get_table_names()) == {"alembic_version", "incidents"}
        assert {column["name"] for column in inspector.get_columns("incidents")} == {
            "id",
            "service",
            "error",
            "log",
            "requested_severity",
            "resolved_severity",
            "classification",
            "probable_cause",
            "recommended_actions",
            "created_at",
        }
        assert {index["name"] for index in inspector.get_indexes("incidents")} == {
            "ix_incidents_classification",
            "ix_incidents_resolved_severity",
            "ix_incidents_service",
        }
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("20260803_0001")
        engine.dispose()
    finally:
        environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
