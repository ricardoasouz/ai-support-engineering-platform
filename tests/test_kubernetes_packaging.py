"""Deterministic repository tests for Phase 8 Kubernetes packaging."""

from pathlib import Path

import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "ai-support-platform"


def test_chart_app_version_matches_authoritative_version() -> None:
    metadata = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    assert metadata["appVersion"] == (ROOT / "VERSION").read_text().strip()
    assert metadata["version"] != metadata["appVersion"]


def test_chart_defaults_do_not_contain_secret_values() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["secrets"]["create"] is False
    assert values["secrets"]["existingSecret"]
    assert not any(values["secrets"]["values"].values())
    assert values["image"]["tag"] != "latest"


def test_external_profile_disables_embedded_stateful_services() -> None:
    values = yaml.safe_load((CHART / "values-external.yaml").read_text())
    assert values["postgres"]["enabled"] is False
    assert values["kafka"]["enabled"] is False
    assert values["ollama"]["enabled"] is False


def test_compose_lifecycle_defaults_remain_enabled() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        KAFKA_ENABLED=False,
    )
    assert settings.run_database_migrations_on_startup is True
    assert settings.run_knowledge_ingestion_on_startup is True


def test_kubernetes_config_disables_per_replica_lifecycle_actions() -> None:
    template = (CHART / "templates" / "configmap.yaml").read_text()
    assert 'RUN_DATABASE_MIGRATIONS_ON_STARTUP: "false"' in template
    assert 'RUN_KNOWLEDGE_INGESTION_ON_STARTUP: "false"' in template
    assert (CHART / "templates" / "migration-job.yaml").is_file()
    assert (CHART / "templates" / "knowledge-ingestion-job.yaml").is_file()


def test_helm_template_actions_are_balanced_and_unescaped() -> None:
    templates = list((CHART / "templates").glob("*"))
    assert templates
    for path in templates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert text.count("{{") == text.count("}}"), path
        assert '\\"' not in text, path


def test_chart_contains_no_forbidden_host_or_privileged_settings() -> None:
    rendered_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CHART / "templates").glob("*.yaml")
    )
    for forbidden in (
        "privileged: true",
        "hostPath:",
        "hostNetwork: true",
        "hostPID: true",
        "hostIPC: true",
        "/var/run/docker.sock",
    ):
        assert forbidden not in rendered_sources
