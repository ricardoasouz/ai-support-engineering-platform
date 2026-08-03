"""Validate an environment file without displaying configuration values."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

# Keep direct `python scripts/validate_config.py` usable on Windows and Linux.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings

_REQUIRED_COMPOSE_KEYS = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "GRAFANA_ADMIN_USER",
    "GRAFANA_ADMIN_PASSWORD",
}
_PLACEHOLDERS = {"change-me", "changeme", "password", "secret"}


def validate_file(path: Path, profile: str | None = None) -> list[str]:
    """Return safe validation errors for one dotenv configuration."""
    if not path.is_file():
        return [f"Configuration file does not exist: {path}"]

    raw = {
        key: value for key, value in dotenv_values(path).items() if value is not None
    }
    effective_profile = profile or raw.get("APP_ENVIRONMENT", "development")
    raw["APP_ENVIRONMENT"] = effective_profile
    errors = [
        f"Missing required setting: {key}"
        for key in sorted(_REQUIRED_COMPOSE_KEYS)
        if not raw.get(key)
    ]

    try:
        Settings(_env_file=None, **raw)
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in item["loc"])
            errors.append(f"Invalid setting {location}: {item['msg']}")

    if effective_profile == "production-like":
        for key in sorted(_REQUIRED_COMPOSE_KEYS):
            if raw.get(key, "").strip().lower() in _PLACEHOLDERS:
                errors.append(f"Production-like setting uses a placeholder: {key}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.example"))
    parser.add_argument(
        "--profile",
        choices=("development", "test", "integration", "production-like"),
    )
    args = parser.parse_args()
    errors = validate_file(args.env_file, args.profile)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Configuration is valid for {args.profile or 'declared'} profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
