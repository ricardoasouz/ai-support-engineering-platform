"""Load and validate the repository-owned knowledge manifest and sources."""

import hashlib
from pathlib import Path

from pydantic import TypeAdapter

from app.knowledge.models import KnowledgeManifestEntry, KnowledgeSource

_MANIFEST_ADAPTER = TypeAdapter(list[KnowledgeManifestEntry])


def content_hash(content: str) -> str:
    """Return a stable SHA-256 digest for normalized text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_knowledge_sources(base_path: Path) -> list[KnowledgeSource]:
    """Load sources in manifest order, rejecting paths outside the base directory."""
    root = base_path.resolve()
    manifest_path = root / "manifest.json"
    entries = _MANIFEST_ADAPTER.validate_json(manifest_path.read_text(encoding="utf-8"))
    sources: list[KnowledgeSource] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.source_id in seen:
            raise ValueError(f"Duplicate knowledge source_id: {entry.source_id}")
        seen.add(entry.source_id)
        source_path = (root / entry.path).resolve()
        if not source_path.is_relative_to(root):
            raise ValueError(f"Knowledge path escapes base directory: {entry.path}")
        raw = source_path.read_text(encoding="utf-8")
        normalized = raw.replace("\r\n", "\n").strip() + "\n"
        sources.append(
            KnowledgeSource(
                source_id=entry.source_id,
                title=entry.title,
                category=entry.category,
                version=entry.version,
                path=entry.path.as_posix(),
                content=normalized,
                content_hash=content_hash(normalized),
            )
        )
    return sources
