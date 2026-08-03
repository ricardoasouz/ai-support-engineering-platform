"""Authoritative application version and safe build metadata."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
_SAFE_GIT_SHA = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_SAFE_BUILD_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _read_version() -> str:
    try:
        value = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0+unknown"
    return (
        value
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", value)
        else "0.0.0+invalid"
    )


__version__ = _read_version()


@dataclass(frozen=True)
class BuildMetadata:
    """Non-sensitive build identifiers supplied by CI when available."""

    version: str
    git_sha: str
    build_time: str


def get_build_metadata() -> BuildMetadata:
    """Return validated build metadata without exposing arbitrary environment data."""
    raw_sha = os.getenv("GIT_SHA", "unknown").strip()
    git_sha = raw_sha if _SAFE_GIT_SHA.fullmatch(raw_sha) else "unknown"
    raw_build_time = os.getenv("BUILD_TIME", "unknown").strip()
    build_time = (
        raw_build_time if _SAFE_BUILD_TIME.fullmatch(raw_build_time) else "unknown"
    )
    return BuildMetadata(
        version=__version__,
        git_sha=git_sha,
        build_time=build_time,
    )
