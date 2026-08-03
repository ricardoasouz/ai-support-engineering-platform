"""Ollama provider contract and failure-handling tests."""

from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

from app.ai.models import GeneratedResolution
from app.ai.providers.base import ProviderResponseError, ProviderUnavailableError
from app.ai.providers.ollama import (
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    _ollama_grammar_schema,
)


class TinyResult(BaseModel):
    answer: str


class LiteralResult(BaseModel):
    action: Literal["tool"]


def test_ollama_llm_uses_schema_and_validates_output() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"answer":"ok"}'}})

    client = httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://ollama"
    )
    provider = OllamaLLMProvider("http://unused", "test-model", 1, client)

    result = provider.generate_structured("prompt", TinyResult)

    assert result == TinyResult(answer="ok")
    assert captured["model"] == "test-model"
    assert captured["stream"] is False
    assert captured["format"] == {
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "type": "object",
    }
    assert captured["options"] == {"temperature": 0}


def test_ollama_llm_rejects_malformed_structured_output() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"content": "not-json"}})
        ),
        base_url="http://ollama",
    )
    provider = OllamaLLMProvider("http://unused", "test-model", 1, client)

    with pytest.raises(ProviderResponseError, match="failed validation"):
        provider.generate_structured("prompt", TinyResult)


def test_ollama_embedding_validates_count_and_dimensions() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
        ),
        base_url="http://ollama",
    )
    provider = OllamaEmbeddingProvider("http://unused", "embed", 3, 1, client)

    assert provider.embed_texts(["one"]) == [[0.1, 0.2, 0.3]]

    mismatch = OllamaEmbeddingProvider("http://unused", "embed", 2, 1, client)
    with pytest.raises(ProviderResponseError, match="dimension mismatch"):
        mismatch.embed_texts(["one"])


def test_ollama_unavailability_is_explicit() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(unavailable), base_url="http://ollama"
    )
    provider = OllamaEmbeddingProvider("http://unused", "embed", 3, 1, client)

    with pytest.raises(ProviderUnavailableError, match="Ollama request failed"):
        provider.embed_texts(["one"])


def test_ollama_grammar_schema_inlines_refs_and_defers_constraints() -> None:
    schema = _ollama_grammar_schema(GeneratedResolution.model_json_schema())

    assert "$defs" not in schema
    citation = schema["properties"]["cited_sources"]["items"]
    assert citation["type"] == "object"
    assert citation["required"] == ["source_id", "chunk_id"]
    assert "exclusiveMinimum" not in citation["properties"]["chunk_id"]
    assert "minItems" not in schema["properties"]["recommended_actions"]


def test_ollama_grammar_converts_literal_const_to_enum() -> None:
    schema = _ollama_grammar_schema(LiteralResult.model_json_schema())

    assert schema["properties"]["action"] == {
        "type": "string",
        "enum": ["tool"],
    }


def test_ollama_provider_closes_injected_client() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    provider = OllamaLLMProvider("http://unused", "test-model", 1, client)

    provider.close()

    assert client.is_closed
