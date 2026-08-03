"""Deterministic paragraph-aware chunking for local runbooks."""

import re

from app.knowledge.loader import content_hash
from app.knowledge.models import KnowledgeChunk


def _split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    words = paragraph.split()
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and current_length + added > max_chars:
            parts.append(" ".join(current))
            current = [word]
            current_length = len(word)
        else:
            current.append(word)
            current_length += added
    if current:
        parts.append(" ".join(current))
    return parts


def chunk_document(content: str, max_chars: int) -> list[KnowledgeChunk]:
    """Pack paragraphs without overlap while preserving deterministic boundaries."""
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()
    ]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
        else:
            units.extend(_split_oversized_paragraph(paragraph, max_chars))

    packed: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}\n\n{unit}"
        if current and len(candidate) > max_chars:
            packed.append(current)
            current = unit
        else:
            current = candidate
    if current:
        packed.append(current)

    return [
        KnowledgeChunk(
            chunk_index=index,
            content=text,
            content_hash=content_hash(text),
        )
        for index, text in enumerate(packed)
    ]
