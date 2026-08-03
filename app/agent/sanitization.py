"""Bound and redact values before audit persistence or model reuse."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)

_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


def sanitize_text(value: str, *, limit: int = 2_000) -> str:
    """Redact common credential shapes and apply a strict length bound."""
    sanitized = value
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        sanitized = pattern.sub("[redacted-sensitive-value]", sanitized)
    return sanitized[:limit]


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """Return JSON-safe, size-bounded data with sensitive-key redaction."""
    if depth >= 5:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)[:120]
            normalized = key.lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                result[key] = "[redacted]"
            else:
                result[key] = sanitize_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:50]]
    return str(value)[:500]


def safe_error(error: Exception) -> str:
    """Persist a bounded type-oriented message without provider response bodies."""
    message = str(error).replace("\n", " ").replace("\r", " ")
    return message[:1_000]
