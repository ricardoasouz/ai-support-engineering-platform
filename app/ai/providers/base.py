"""Provider-neutral contracts used by grounded AI workflows."""

from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class ProviderCapabilities(BaseModel):
    """Explicit adapter capabilities used instead of provider assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    structured_output: bool
    native_tool_selection: bool
    streaming: bool
    token_usage: bool


class ProviderError(RuntimeError):
    """Base error raised by an AI provider adapter."""


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached or timed out."""


class ProviderResponseError(ProviderError):
    """The provider returned an unusable or schema-incompatible response."""


class LLMProvider(Protocol):
    """Generate a response that has been validated against a Pydantic schema."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    def last_usage(self) -> dict[str, object] | None: ...

    def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel: ...


class EmbeddingProvider(Protocol):
    """Generate fixed-width embeddings for one batch of text."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
