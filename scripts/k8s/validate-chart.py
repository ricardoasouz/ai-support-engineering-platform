"""Validate chart metadata and repository-safe value defaults."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART_ROOT = ROOT / "deploy" / "helm" / "ai-support-platform"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].+)?$")


def load_yaml(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a YAML mapping")
    return value


def main() -> None:
    schema = json.loads((CHART_ROOT / "values.schema.json").read_text(encoding="utf-8"))
    chart = load_yaml(CHART_ROOT / "Chart.yaml")
    values = load_yaml(CHART_ROOT / "values.yaml")
    app_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    if chart.get("apiVersion") != "v2" or chart.get("type") != "application":
        raise ValueError("the Helm chart must be a v2 application chart")
    if schema.get("type") != "object":
        raise ValueError("values.schema.json must validate a top-level object")
    if chart.get("appVersion") != app_version:
        raise ValueError("Chart.appVersion must match the authoritative VERSION file")
    if not SEMVER.fullmatch(str(chart.get("version", ""))):
        raise ValueError("Chart.version must be semantic")

    image = values.get("image")
    if not isinstance(image, dict) or image.get("tag") in {None, "", "latest"}:
        raise ValueError("values.image.tag must be set and must not use latest")

    secrets = values.get("secrets")
    if not isinstance(secrets, dict):
        raise TypeError("values.secrets must be configured")
    secret_values = secrets.get("values")
    if not isinstance(secret_values, dict) or any(secret_values.values()):
        raise ValueError("committed Helm values must not contain secret values")
    if secrets.get("create") is not False or not secrets.get("existingSecret"):
        raise ValueError("safe defaults must reference an externally created Secret")

    external = load_yaml(CHART_ROOT / "values-external.yaml")
    for component in ("postgres", "kafka", "ollama"):
        config = external.get(component)
        if not isinstance(config, dict) or config.get("enabled") is not False:
            raise ValueError(f"external mode must disable embedded {component}")

    print("Helm chart metadata and values are valid.")


if __name__ == "__main__":
    main()
