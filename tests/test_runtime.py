"""Shell-free container role dispatch tests."""

import sys
from types import SimpleNamespace

from app import runtime


def test_runtime_dispatches_api_role(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(runtime, "run_api", lambda: called.append("api"))

    runtime.main(["api"])

    assert called == ["api"]


def test_runtime_dispatches_worker_role(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(runtime, "run_worker", lambda: called.append("worker"))

    runtime.main(["worker"])

    assert called == ["worker"]


def test_kubernetes_api_startup_skips_per_replica_migration(monkeypatch) -> None:
    calls: list[str] = []
    settings = SimpleNamespace(
        run_database_migrations_on_startup=False,
        api_workers=1,
    )
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(
        runtime.command,
        "upgrade",
        lambda *_args: calls.append("migration"),
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda *_args, **_kwargs: calls.append("api")),
    )

    runtime.run_api()

    assert calls == ["api"]


def test_kubernetes_worker_startup_skips_per_replica_ingestion(monkeypatch) -> None:
    from app.knowledge import ingest
    from app.workers import incident_worker

    calls: list[str] = []
    settings = SimpleNamespace(run_knowledge_ingestion_on_startup=False)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "main", lambda: calls.append("ingestion"))
    monkeypatch.setattr(incident_worker, "main", lambda: calls.append("worker"))

    runtime.run_worker()

    assert calls == ["worker"]
