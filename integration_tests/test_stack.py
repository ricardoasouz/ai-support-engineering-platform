"""Black-box incident flow against the isolated integration Compose stack."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 for the Docker-backed test",
    ),
]

BASE_URL = os.getenv("INTEGRATION_BASE_URL", "http://127.0.0.1:18000")


def request_json(
    method: str, path: str, payload: dict[str, object] | None = None
) -> tuple[int, object]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_incident_reaches_grounded_reviewable_resolution() -> None:
    health_status, health = request_json("GET", "/health")
    assert health_status == 200
    assert health == {
        "status": "healthy",
        "service": "ai-support-engineering-platform",
    }
    build_status, build = request_json("GET", "/build")
    assert build_status == 200
    assert build["version"] == "0.7.0"

    service = f"integration-billing-{uuid4().hex[:10]}"
    create_status, analysis = request_json(
        "POST",
        "/api/v1/incidents",
        {
            "service": service,
            "error": "PostgreSQL connection pool exhausted",
            "log": "pool timeout while waiting for an available database connection",
            "severity": "critical",
        },
    )
    assert create_status == 200
    assert analysis["classification"] == "database_connection_error"

    query = urllib.parse.urlencode({"service": service})
    list_status, incidents = request_json("GET", f"/api/v1/incidents?{query}")
    assert list_status == 200
    assert len(incidents) == 1
    incident_id = incidents[0]["id"]

    deadline = time.monotonic() + float(os.getenv("INTEGRATION_TIMEOUT_SECONDS", "90"))
    execution: dict[str, object] | None = None
    resolution: dict[str, object] | None = None
    while time.monotonic() < deadline:
        execution_status, execution_body = request_json(
            "GET", f"/api/v1/incidents/{incident_id}/agent-execution"
        )
        resolution_status, resolution_body = request_json(
            "GET", f"/api/v1/incidents/{incident_id}/resolution"
        )
        if execution_status == 200 and resolution_status == 200:
            execution = execution_body
            resolution = resolution_body
            if execution["status"] == "awaiting_review":
                break
        time.sleep(1)

    assert execution is not None
    assert resolution is not None
    assert execution["status"] == "awaiting_review"
    assert resolution["status"] == "completed"
    assert resolution["cited_sources"]
    assert resolution["tools_used"] == ["get_incident", "retrieve_runbooks"]

    context_status, context = request_json(
        "GET", f"/api/v1/incidents/{incident_id}/resolution/context"
    )
    assert context_status == 200
    assert context["context"]
