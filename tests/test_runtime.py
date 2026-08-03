"""Shell-free container role dispatch tests."""

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
