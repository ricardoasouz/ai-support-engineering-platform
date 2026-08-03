"""Knowledge ingestion and retrieval value objects."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeManifestEntry(BaseModel):
    """Validated manifest metadata for one bundled source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    path: Path
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)


class KnowledgeSource(BaseModel):
    """Loaded source content with stable citation metadata."""

    source_id: str
    title: str
    category: str
    version: str
    path: str
    content: str
    content_hash: str


class KnowledgeChunk(BaseModel):
    """Deterministic source chunk before persistence."""

    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)


class RetrievedChunk(BaseModel):
    """A scored, citation-addressable chunk returned by semantic search."""

    chunk_id: int
    source_id: str
    title: str
    category: str
    content: str
    similarity: float

    @property
    def citation_key(self) -> tuple[str, int]:
        return self.source_id, self.chunk_id
