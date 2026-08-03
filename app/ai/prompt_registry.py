"""Allowlisted, versioned prompt loading without persisting prompt contents."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


class PromptTemplateError(ValueError):
    """A prompt file or render request violated the prompt allowlist."""


@dataclass(frozen=True)
class PromptTemplate:
    """One validated prompt template identified by stable name and version."""

    name: str
    version: str
    content: str

    def render(self, values: dict[str, str]) -> str:
        """Render only explicitly named placeholders and reject omissions."""
        rendered = self.content
        placeholders = set(re.findall(r"\{\{([a-z_]+)\}\}", rendered))
        missing = placeholders - values.keys()
        extra = values.keys() - placeholders
        if missing or extra:
            raise PromptTemplateError(
                f"Prompt values do not match template: missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        if re.search(r"\{\{[a-z_]+\}\}", rendered):
            raise PromptTemplateError("Prompt contains an unresolved placeholder")
        return rendered


class PromptRegistry:
    """Load only image-bundled templates from a fixed allowlist."""

    _ALLOWED: ClassVar[dict[tuple[str, str], str]] = {
        ("planner", "v1"): "planner/v1.txt",
        ("resolver", "v1"): "resolver/v1.txt",
        ("evidence_summary", "v1"): "evidence_summary/v1.txt",
    }

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("prompts")

    def load(self, name: str, version: str) -> PromptTemplate:
        """Return a validated prompt or reject an unknown name/version."""
        relative_path = self._ALLOWED.get((name, version))
        if relative_path is None:
            raise PromptTemplateError(f"Prompt is not allowlisted: {name}/{version}")
        path = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise PromptTemplateError("Prompt path escaped the prompt directory")
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptTemplateError(
                f"Prompt file is unavailable: {name}/{version}"
            ) from exc
        if not content or len(content) > 20_000:
            raise PromptTemplateError("Prompt file is empty or oversized")
        return PromptTemplate(name=name, version=version, content=content)
