"""Typed allowlist, sanitization, authorization, and tool-budget tests."""

import time
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agent.errors import (
    ToolExecutionError,
    ToolTimeoutError,
    UnauthorizedToolArgumentsError,
    UnknownToolError,
)
from app.agent.models import ToolRequest
from app.agent.tools import (
    GetIncidentInput,
    StrictToolInput,
    ToolContext,
    ToolExecutor,
    ToolOutput,
    ToolRegistry,
)
from app.db.models import AgentExecutionRecord, AIResolutionRecord, IncidentRecord


class UnusedRetriever:
    pass


def persist_incident(
    session_factory: sessionmaker[Session], *, log: str = "secret raw log"
) -> IncidentRecord:
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log=log,
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired token",
            recommended_actions=["Refresh token"],
        )
        session.add(incident)
        session.commit()
        return incident


def context_for(
    session_factory: sessionmaker[Session], incident_id: int
) -> ToolContext:
    return ToolContext(
        incident_id=incident_id,
        session_factory=session_factory,
        retriever=UnusedRetriever(),  # type: ignore[arg-type]
    )


def test_get_incident_omits_raw_log(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory, log="Bearer secret-token-value")
    executor = ToolExecutor(ToolRegistry(), timeout_seconds=1, max_tool_calls=2)

    result = executor.execute(
        ToolRequest(tool_name="get_incident", arguments={"incident_id": incident.id}),
        context_for(session_factory, incident.id),
        current_tool_calls=0,
    )

    assert result.status == "succeeded"
    assert result.data["raw_log_omitted"] is True
    assert "secret-token-value" not in result.model_dump_json()


def test_unknown_invalid_oversized_and_cross_incident_calls_are_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    executor = ToolExecutor(ToolRegistry(), timeout_seconds=1, max_tool_calls=3)
    context = context_for(session_factory, incident.id)

    with pytest.raises(UnknownToolError):
        executor.execute(
            ToolRequest(tool_name="run_shell", arguments={}),
            context,
            current_tool_calls=0,
        )
    with pytest.raises(ToolExecutionError, match="Invalid arguments"):
        executor.execute(
            ToolRequest(
                tool_name="get_incident",
                arguments={"incident_id": incident.id, "sql": "SELECT *"},
            ),
            context,
            current_tool_calls=0,
        )
    with pytest.raises(ToolExecutionError, match="size limit"):
        executor.execute(
            ToolRequest(
                tool_name="retrieve_runbooks", arguments={"query": "x" * 9_000}
            ),
            context,
            current_tool_calls=0,
        )
    with pytest.raises(UnauthorizedToolArgumentsError):
        executor.execute(
            ToolRequest(
                tool_name="get_incident",
                arguments={"incident_id": incident.id + 1},
            ),
            context,
            current_tool_calls=0,
        )


class SlowTool:
    name = "slow_read"
    description = "Test-only slow read."
    input_model = GetIncidentInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        time.sleep(0.03)
        return ToolOutput(summary="eventually completed")


def test_tool_timeout_is_explicit(session_factory: sessionmaker[Session]) -> None:
    incident = persist_incident(session_factory)
    executor = ToolExecutor(
        ToolRegistry([SlowTool()]), timeout_seconds=0.001, max_tool_calls=1
    )

    with pytest.raises(ToolTimeoutError):
        executor.execute(
            ToolRequest(tool_name="slow_read", arguments={"incident_id": incident.id}),
            context_for(session_factory, incident.id),
            current_tool_calls=0,
        )


def test_registry_contains_only_phase5_allowlist() -> None:
    assert ToolRegistry().names == {
        "get_incident",
        "retrieve_runbooks",
        "search_similar_incidents",
        "get_previous_resolutions",
        "get_incident_resolution_context",
        "summarize_evidence",
    }


def test_sanitization_redacts_credentials_without_masking_generic_wording() -> None:
    from app.agent.sanitization import sanitize_text

    assert sanitize_text("Send a valid bearer token.") == "Send a valid bearer token."
    assert "abc123" not in sanitize_text("password=abc123")
    assert "verylongcredentialvalue" not in sanitize_text(
        "Bearer verylongcredentialvalue"
    )


def test_rejected_resolution_is_not_returned_as_historical_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    current = persist_incident(session_factory)
    prior = persist_incident(session_factory)
    event_id = str(uuid4())
    with session_factory() as session:
        session.add(
            AIResolutionRecord(
                incident_id=prior.id,
                event_id=event_id,
                status="completed",
                attempt_count=1,
                summary="Rejected resolution",
                root_cause="Not accepted",
                recommended_actions=["Do not reuse"],
                confidence=0.4,
                cited_sources=[],
                retrieved_context=[],
            )
        )
        session.add(
            AgentExecutionRecord(
                execution_id=str(uuid4()),
                incident_id=prior.id,
                event_id=event_id,
                status="rejected",
                provider="fake",
                model="fake",
                planner_prompt_name="planner",
                planner_prompt_version="v1",
                resolver_prompt_name="resolver",
                resolver_prompt_version="v1",
            )
        )
        session.commit()
    result = ToolExecutor(ToolRegistry(), timeout_seconds=1, max_tool_calls=2).execute(
        ToolRequest(
            tool_name="get_previous_resolutions",
            arguments={"incident_id": current.id},
        ),
        context_for(session_factory, current.id),
        current_tool_calls=0,
    )

    assert result.data == {"resolutions": []}
