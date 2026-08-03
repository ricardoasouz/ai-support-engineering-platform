"""First-class self-hosted Ollama LLM and embedding adapters."""

import math
import time
from typing import Any

import httpx
from opentelemetry.trace import SpanKind
from pydantic import ValidationError

from app.ai.providers.base import (
    ProviderCapabilities,
    ProviderResponseError,
    ProviderUnavailableError,
    StructuredModel,
    StructuredOutputValidationError,
)
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span

_OLLAMA_GRAMMAR_KEYS = {"type", "properties", "required", "items", "enum"}


def _ollama_grammar_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline references and retain the JSON-shape subset Ollama can compile.

    Pydantic validates the original model after generation, so string lengths,
    numeric ranges, array bounds, and extra-field policy remain enforced locally.
    """
    definitions = schema.get("$defs", {})

    def simplify(node: object) -> object:
        if isinstance(node, list):
            return [simplify(item) for item in node]
        if not isinstance(node, dict):
            return node
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            target = definitions.get(name)
            if isinstance(target, dict):
                return simplify(target)
        result: dict[str, object] = {}
        if "const" in node:
            result["enum"] = [simplify(node["const"])]
        for key, value in node.items():
            if key not in _OLLAMA_GRAMMAR_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                result[key] = {
                    property_name: simplify(property_schema)
                    for property_name, property_schema in value.items()
                }
            else:
                result[key] = simplify(value)
        return result

    simplified = simplify(schema)
    if not isinstance(simplified, dict):
        raise ProviderResponseError("Could not build an Ollama JSON grammar schema")
    return simplified


class _OllamaHTTPClient:
    """Small synchronous transport shared by provider-specific adapters."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    def post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        try:
            response = self.client.post(path, json=payload)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise ProviderUnavailableError(f"Ollama request failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            error_type = (
                ProviderUnavailableError
                if exc.response.status_code == 429 or exc.response.status_code >= 500
                else ProviderResponseError
            )
            raise error_type(
                f"Ollama returned HTTP {exc.response.status_code}: {detail}"
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama returned non-JSON content") from exc
        if not isinstance(body, dict):
            raise ProviderResponseError("Ollama returned a non-object JSON response")
        return body

    def close(self) -> None:
        """Close pooled HTTP connections."""
        self.client.close()


class OllamaLLMProvider:
    """Use Ollama's chat endpoint with native JSON-schema constraints."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._http = _OllamaHTTPClient(base_url, timeout_seconds, client)
        self._last_usage: dict[str, object] | None = None

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Declare only behavior implemented by this adapter."""
        return ProviderCapabilities(
            structured_output=True,
            native_tool_selection=False,
            streaming=False,
            token_usage=True,
        )

    @property
    def last_usage(self) -> dict[str, object] | None:
        """Return provider-reported aggregate counts from the last call."""
        return self._last_usage

    def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        """Generate structured output with safe provider telemetry."""
        started = time.perf_counter()
        status = "failure"
        self._last_usage = None
        metric_attributes = {
            "provider": self.provider_name,
            "model": self.model_name,
            "operation": "chat",
        }
        with start_span(
            "ollama chat",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.provider.name": self.provider_name,
                "gen_ai.request.model": self.model_name,
                "gen_ai.operation.name": "chat",
            },
        ) as span:
            try:
                response = self._generate_structured(prompt, response_model)
                status = "success"
                return response
            except Exception as exc:
                mark_span_error(span, exc)
                get_metrics().count("llm_failures", attributes=metric_attributes)
                raise
            finally:
                request_attributes = {**metric_attributes, "status": status}
                get_metrics().count("llm_requests", attributes=request_attributes)
                get_metrics().observe(
                    "llm_request_duration_seconds",
                    time.perf_counter() - started,
                    request_attributes,
                )
                if status == "success" and self._last_usage is not None:
                    prompt_tokens = self._last_usage.get("prompt_eval_count")
                    completion_tokens = self._last_usage.get("eval_count")
                    if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                        get_metrics().count(
                            "llm_prompt_tokens", prompt_tokens, metric_attributes
                        )
                    if isinstance(completion_tokens, int) and completion_tokens >= 0:
                        get_metrics().count(
                            "llm_completion_tokens",
                            completion_tokens,
                            metric_attributes,
                        )

    def _generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        schema = response_model.model_json_schema()
        grammar_schema = _ollama_grammar_schema(schema)
        body = self._http.post(
            "/api/chat",
            {
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a support engineering assistant. Use only the "
                            "provided evidence and return the requested JSON schema."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "format": grammar_schema,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        self._last_usage = {
            key: body[key]
            for key in (
                "prompt_eval_count",
                "eval_count",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
            )
            if isinstance(body.get(key), (int, float))
        } or None
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderResponseError("Ollama chat response has no message content")
        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            raise StructuredOutputValidationError(
                f"Ollama structured output failed validation: {exc}"
            ) from exc

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._http.close()


class OllamaEmbeddingProvider:
    """Use Ollama's batch embedding endpoint with strict dimension validation."""

    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._http = _OllamaHTTPClient(base_url, timeout_seconds, client)

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        started = time.perf_counter()
        status = "failure"
        metric_attributes = {
            "provider": self.provider_name,
            "model": self.model_name,
            "operation": "embedding",
        }
        with start_span(
            "ollama embedding",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.provider.name": self.provider_name,
                "gen_ai.request.model": self.model_name,
                "gen_ai.operation.name": "embedding",
                "gen_ai.request.input_count": len(texts),
            },
        ) as span:
            try:
                result = self._embed_texts(texts)
                status = "success"
                return result
            except Exception as exc:
                mark_span_error(span, exc)
                get_metrics().count("llm_failures", attributes=metric_attributes)
                raise
            finally:
                request_attributes = {**metric_attributes, "status": status}
                get_metrics().count("llm_requests", attributes=request_attributes)
                get_metrics().observe(
                    "llm_request_duration_seconds",
                    time.perf_counter() - started,
                    request_attributes,
                )

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Call the batch embedding API and validate every returned vector."""
        body = self._http.post(
            "/api/embed",
            {"model": self._model, "input": texts, "truncate": True},
        )
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ProviderResponseError(
                "Ollama returned an unexpected number of embeddings"
            )

        validated: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                actual = len(vector) if isinstance(vector, list) else "non-list"
                raise ProviderResponseError(
                    "Embedding dimension mismatch: "
                    f"expected {self._dimensions}, received {actual}"
                )
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    "Ollama embedding contains a non-numeric value"
                ) from exc
            if not all(math.isfinite(value) for value in values):
                raise ProviderResponseError(
                    "Ollama embedding contains a non-finite value"
                )
            validated.append(values)
        return validated

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._http.close()
