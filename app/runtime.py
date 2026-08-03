"""Shell-free container entrypoints for the API and worker roles."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings

_ROOT = Path(__file__).resolve().parents[1]


def run_api() -> None:
    """Validate configuration, migrate once, and start Uvicorn."""
    settings = get_settings()
    alembic_config = Config(str(_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        workers=settings.api_workers,
        access_log=False,
    )


def run_worker() -> None:
    """Validate configuration, ingest bundled knowledge, and start consumption."""
    get_settings()
    from app.knowledge.ingest import main as ingest_knowledge
    from app.workers.incident_worker import main as run_incident_worker

    ingest_knowledge()
    run_incident_worker()


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker"))
    role = parser.parse_args(arguments).role
    if role == "api":
        run_api()
    else:
        run_worker()


if __name__ == "__main__":
    main()
