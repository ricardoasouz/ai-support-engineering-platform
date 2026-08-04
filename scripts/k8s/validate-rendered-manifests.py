"""Apply lightweight security policy assertions to Helm-rendered manifests."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

PUBLIC_COMPONENTS = {"api", "grafana"}
INTERNAL_SERVICES = {"postgres", "kafka", "ollama", "prometheus", "tempo"}
APPLICATION_COMPONENTS = {"api", "worker"}


def pod_spec(document: dict[str, Any]) -> dict[str, Any] | None:
    kind = document.get("kind")
    spec = document.get("spec", {})
    if kind in {"Deployment", "StatefulSet", "DaemonSet"}:
        return spec.get("template", {}).get("spec", {})
    if kind == "Job":
        return spec.get("template", {}).get("spec", {})
    if kind == "Pod":
        return spec
    return None


def component(document: dict[str, Any]) -> str:
    return str(
        document.get("metadata", {})
        .get("labels", {})
        .get("app.kubernetes.io/component", "")
    )


def validate_document(document: dict[str, Any], errors: list[str]) -> None:
    kind = str(document.get("kind", "unknown"))
    name = str(document.get("metadata", {}).get("name", "unnamed"))
    identity = f"{kind}/{name}"

    if kind == "Secret" and (document.get("data") or document.get("stringData")):
        errors.append(f"{identity}: rendered output contains Secret values")

    if kind in {"Role", "ClusterRole"}:
        for rule in document.get("rules", []):
            if "*" in rule.get("verbs", []) or "*" in rule.get("resources", []):
                errors.append(f"{identity}: wildcard RBAC is forbidden")

    if kind == "Service":
        service_component = component(document)
        service_type = document.get("spec", {}).get("type", "ClusterIP")
        if service_component in INTERNAL_SERVICES and service_type != "ClusterIP":
            errors.append(f"{identity}: internal service must remain ClusterIP")
        if (
            service_type in {"LoadBalancer", "ExternalName"}
            and service_component not in PUBLIC_COMPONENTS
        ):
            errors.append(f"{identity}: unexpected public-facing service type")

    spec = pod_spec(document)
    if spec is None:
        return
    workload_component = component(document)
    if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"):
        errors.append(f"{identity}: host namespace sharing is forbidden")
    if spec.get("automountServiceAccountToken") is not False:
        errors.append(f"{identity}: service account token automount must be disabled")

    for volume in spec.get("volumes", []):
        if "hostPath" in volume:
            errors.append(f"{identity}: hostPath volumes are forbidden")

    pod_security = spec.get("securityContext", {})
    if workload_component in APPLICATION_COMPONENTS:
        if pod_security.get("runAsNonRoot") is not True:
            errors.append(f"{identity}: application pod must run as non-root")
        if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
            errors.append(
                f"{identity}: application pod must use RuntimeDefault seccomp"
            )

    for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
        container_name = container.get("name", "unnamed")
        prefix = f"{identity} container {container_name}"
        security = container.get("securityContext", {})
        if security.get("privileged") is True:
            errors.append(f"{prefix}: privileged mode is forbidden")
        if security.get("allowPrivilegeEscalation") is not False:
            errors.append(f"{prefix}: privilege escalation must be disabled")
        if security.get("capabilities", {}).get("drop") != ["ALL"]:
            errors.append(f"{prefix}: all Linux capabilities must be dropped")
        if (
            workload_component in APPLICATION_COMPONENTS
            and security.get("readOnlyRootFilesystem") is not True
        ):
            errors.append(f"{prefix}: application root filesystem must be read-only")
        resources = container.get("resources", {})
        if not resources.get("requests") or not resources.get("limits"):
            errors.append(f"{prefix}: resource requests and limits are required")
        for mount in container.get("volumeMounts", []):
            if mount.get("mountPath") == "/var/run/docker.sock":
                errors.append(f"{prefix}: Docker socket mounts are forbidden")

    if workload_component == "api":
        container = spec.get("containers", [{}])[0]
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            if probe not in container:
                errors.append(f"{identity}: API requires {probe}")
    if workload_component == "worker":
        container = spec.get("containers", [{}])[0]
        if "readinessProbe" not in container or "livenessProbe" not in container:
            errors.append(f"{identity}: worker requires readiness and liveness probes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    documents = [
        item
        for item in yaml.safe_load_all(args.manifest.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]
    if not documents:
        raise ValueError("rendered manifest contains no Kubernetes resources")

    errors: list[str] = []
    for document in documents:
        validate_document(document, errors)
    if errors:
        raise ValueError(
            "Kubernetes policy validation failed:\n- " + "\n- ".join(errors)
        )
    print(f"Validated {len(documents)} rendered Kubernetes resources.")


if __name__ == "__main__":
    main()
