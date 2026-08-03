"""Construct configured production AI providers."""

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.ai.providers.fake import (
    DeterministicFakeEmbeddingProvider,
    DeterministicFakeLLMProvider,
)
from app.ai.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
from app.core.config import Settings


def create_llm_provider(settings: Settings) -> LLMProvider:
    """Create the selected LLM adapter."""
    if settings.llm_provider == "ollama":
        return OllamaLLMProvider(
            settings.ollama_base_url,
            settings.ollama_llm_model,
            settings.ollama_request_timeout_seconds,
        )
    if settings.llm_provider == "fake":
        return DeterministicFakeLLMProvider()
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Create the selected embedding adapter."""
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddingProvider(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            settings.embedding_dimensions,
            settings.ollama_request_timeout_seconds,
        )
    if settings.embedding_provider == "fake":
        return DeterministicFakeEmbeddingProvider(settings.embedding_dimensions)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
