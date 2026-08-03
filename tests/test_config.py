"""Fail-fast configuration and deterministic CI provider tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.models import AgentFinalResponse
from app.ai.providers.factory import create_embedding_provider, create_llm_provider
from app.ai.providers.fake import (
    DeterministicFakeEmbeddingProvider,
    DeterministicFakeLLMProvider,
)
from app.core.config import Settings
from scripts.validate_config import validate_file


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "KAFKA_ENABLED": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_fake_providers_require_explicit_test_or_integration_opt_in() -> None:
    with pytest.raises(ValidationError, match="ALLOW_FAKE_PROVIDERS"):
        settings(LLM_PROVIDER="fake")
    with pytest.raises(ValidationError, match="limited to test and integration"):
        settings(
            APP_ENVIRONMENT="development",
            ALLOW_FAKE_PROVIDERS=True,
            LLM_PROVIDER="fake",
        )


def test_fake_providers_are_deterministic_and_selected_in_integration() -> None:
    configured = settings(
        APP_ENVIRONMENT="integration",
        ALLOW_FAKE_PROVIDERS=True,
        LLM_PROVIDER="fake",
        EMBEDDING_PROVIDER="fake",
        EMBEDDING_DIMENSIONS=8,
    )

    llm = create_llm_provider(configured)
    embeddings = create_embedding_provider(configured)

    assert isinstance(llm, DeterministicFakeLLMProvider)
    assert isinstance(embeddings, DeterministicFakeEmbeddingProvider)
    assert (
        embeddings.embed_texts(["stable"])[0] == embeddings.embed_texts(["stable"])[0]
    )
    assert len(embeddings.embed_texts(["stable"])[0]) == 8


def test_fake_resolver_reads_sorted_server_citation_json() -> None:
    provider = DeterministicFakeLLMProvider()
    prompt = """AVAILABLE CITATIONS
[{"chunk_id":4,"source_id":"runbook-postgresql-connectivity"}]
TOOLS ACTUALLY USED
["get_incident","retrieve_runbooks"]"""

    result = provider.generate_structured(prompt, AgentFinalResponse)

    assert result.cited_sources[0].source_id == "runbook-postgresql-connectivity"
    assert result.cited_sources[0].chunk_id == 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"APP_ENVIRONMENT": "production-like"},
            "requires PostgreSQL",
        ),
        (
            {
                "KAFKA_PRODUCER_ENABLE_IDEMPOTENCE": True,
                "KAFKA_PRODUCER_ACKS": "1",
            },
            "acknowledgments=all",
        ),
        (
            {"AGENT_MAX_STEPS": 6, "AGENT_MAX_TOOL_CALLS": 6},
            "reserve capacity",
        ),
        (
            {
                "AGENT_MAX_DURATION_SECONDS": 30,
                "AGENT_TOOL_TIMEOUT_SECONDS": 31,
            },
            "cannot exceed total agent duration",
        ),
        ({"API_WORKERS": 17}, "less than or equal to 16"),
    ],
)
def test_unsafe_cross_field_configuration_is_rejected(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**overrides)


def test_example_configuration_is_valid() -> None:
    assert validate_file(Path(".env.example")) == []


def test_production_like_validation_rejects_placeholders() -> None:
    errors = validate_file(Path(".env.example"), "production-like")

    assert any("POSTGRES_PASSWORD" in error for error in errors)
    assert any("GRAFANA_ADMIN_PASSWORD" in error for error in errors)
