"""Deterministic controlled-agent loop, guardrail, and recovery tests."""

from collections import deque
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.models import AgentFinalResponse, PlannerDecision
from app.agent.workflow import (
    AgentLimits,
    ControlledAgentWorkflow,
    RequiredIncidentDecision,
    RequiredRetrievalDecision,
)
from app.ai.providers.base import (
    ProviderCapabilities,
    ProviderUnavailableError,
    StructuredOutputValidationError,
)
from app.db.models import (
    AgentExecutionRecord,
    AgentStepRecord,
    AIResolutionRecord,
    IncidentRecord,
    ProcessedEventRecord,
)
from app.events.models import IncidentCreatedEvent
from app.knowledge.models import RetrievedChunk
from app.models.incident import IncidentClassification, Severity
from app.workers.processor import (
    IncidentEventProcessor,
    ProcessingOutcome,
    RetryableProcessingError,
)


class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-768"
    dimensions = 768


class FakeRetriever:
    embedding_provider = FakeEmbeddingProvider()

    def __init__(
        self, *, empty: bool = False, category: str = "authentication"
    ) -> None:
        self.empty = empty
        self.category = category

    def retrieve_query(
        self,
        query_text: str,
        *,
        top_k: int | None = None,
        category: str | None = None,
    ) -> list[RetrievedChunk]:
        assert len(query_text) <= 2_000
        assert category == self.category
        if self.empty:
            return []
        source_id = {
            "authentication": "runbook-jwt-authentication",
            "database": "runbook-postgresql-connectivity",
            "http": "runbook-http-timeouts",
        }.get(self.category, "runbook-generic")
        return [
            RetrievedChunk(
                chunk_id=7,
                source_id=source_id,
                title=f"{self.category.title()} Runbook",
                category=category or "authentication",
                content="Use bounded diagnostics from the approved local runbook.",
                similarity=0.95,
            )
        ][:top_k]


class ScriptedProvider:
    provider_name = "fake"
    model_name = "structured-fake"
    capabilities = ProviderCapabilities(
        structured_output=True,
        native_tool_selection=False,
        streaming=False,
        token_usage=True,
    )
    last_usage: ClassVar[dict[str, object] | None] = {"eval_count": 3}

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = deque(responses)
        self.calls = 0

    def generate_structured(
        self, prompt: str, response_model: type[BaseModel]
    ) -> BaseModel:
        self.calls += 1
        assert "raw-super-secret" not in prompt
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        try:
            return response_model.model_validate(response)
        except ValidationError as exc:
            raise StructuredOutputValidationError(
                f"Structured output failed validation: {exc}"
            ) from exc


def tool_plan(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "goal": "Gather bounded evidence for this incident.",
        "next_action": "tool",
        "tool_name": name,
        "tool_arguments": arguments,
        "reason_summary": f"Collect evidence with {name}.",
        "expected_evidence": "Sanitized support evidence.",
    }


def final_plan() -> dict[str, object]:
    return {
        "goal": "Produce a grounded resolution.",
        "next_action": "final",
        "tool_name": None,
        "tool_arguments": None,
        "reason_summary": "Required evidence has been gathered.",
        "expected_evidence": "A cited, reviewable final response.",
    }


def final_response(
    *,
    bad_citation: bool = False,
    insufficient: bool = False,
    source_id: str = "runbook-jwt-authentication",
) -> dict[str, object]:
    return {
        "summary": "The request used an expired JWT.",
        "root_cause": "The token lifetime elapsed.",
        "recommended_actions": ["Have an operator refresh the token and retry."],
        "confidence": 0.92,
        "cited_sources": (
            []
            if insufficient
            else [
                {
                    "source_id": "invented" if bad_citation else source_id,
                    "chunk_id": 7,
                }
            ]
        ),
        "evidence_summary": "The incident and authentication runbook agree.",
        "tools_used": ["get_incident", "retrieve_runbooks"],
        "limitations": [],
        "escalation_required": False,
        "human_review_recommended": True,
    }


def persist_incident(session_factory: sessionmaker[Session]) -> IncidentRecord:
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log="raw-super-secret token payload",
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired token",
            recommended_actions=["Refresh token"],
        )
        session.add(incident)
        session.commit()
        return incident


def event_for(incident_id: int) -> IncidentCreatedEvent:
    return IncidentCreatedEvent.create(
        incident_id=incident_id,
        service="identity-api",
        classification=IncidentClassification.AUTHENTICATION_ERROR,
        severity=Severity.HIGH,
    )


def persist_database_pool_incident(
    session_factory: sessionmaker[Session],
) -> IncidentRecord:
    """Reproduce the sanitized fields from the live billing pool incident."""
    with session_factory() as session:
        incident = IncidentRecord(
            service="billing-api",
            error="PostgreSQL connection pool exhausted",
            log="pool timeout while waiting for an available database connection",
            requested_severity="critical",
            resolved_severity="critical",
            classification="database_connection_error",
            probable_cause="The database connection pool has no available capacity.",
            recommended_actions=["Inspect pool utilization and slow transactions."],
        )
        session.add(incident)
        session.commit()
        return incident


def database_event_for(incident_id: int) -> IncidentCreatedEvent:
    return IncidentCreatedEvent.create(
        incident_id=incident_id,
        service="billing-api",
        classification=IncidentClassification.DATABASE_CONNECTION_ERROR,
        severity=Severity.CRITICAL,
    )


def workflow_for(
    session_factory: sessionmaker[Session],
    provider: ScriptedProvider,
    *,
    empty_retrieval: bool = False,
    retrieval_category: str = "authentication",
    repairs: int = 2,
) -> ControlledAgentWorkflow:
    return ControlledAgentWorkflow(
        session_factory,
        FakeRetriever(  # type: ignore[arg-type]
            empty=empty_retrieval,
            category=retrieval_category,
        ),
        provider,  # type: ignore[arg-type]
        AgentLimits(
            max_steps=8,
            max_tool_calls=6,
            max_repeated_tool_calls=1,
            max_retrieval_chunks=4,
            max_duration_seconds=30,
            tool_timeout_seconds=2,
            model_retries=3,
            repair_attempts=repairs,
        ),
    )


def success_script(incident_id: int) -> list[dict[str, object]]:
    return [
        tool_plan("get_incident", {"incident_id": incident_id}),
        tool_plan(
            "retrieve_runbooks",
            {
                "query": "JWT expired issuer audience",
                "classification": "authentication_error",
                "top_k": 4,
            },
        ),
        final_plan(),
        final_response(),
    ]


def test_agent_resolution_is_auditable_and_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    provider = ScriptedProvider(success_script(incident.id))
    event = event_for(incident.id)
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, provider),
    )

    assert processor.process(event) is ProcessingOutcome.PROCESSED
    assert processor.process(event) is ProcessingOutcome.DUPLICATE

    with session_factory() as session:
        execution = session.scalar(select(AgentExecutionRecord))
        resolution = session.scalar(select(AIResolutionRecord))
        steps = list(
            session.scalars(
                select(AgentStepRecord).order_by(AgentStepRecord.step_number)
            )
        )
        processed = session.get(ProcessedEventRecord, str(event.event_id))
    assert execution is not None
    assert execution.status == "awaiting_review"
    assert execution.step_count == 3
    assert execution.tool_call_count == 2
    assert execution.model_call_count == 4
    assert resolution is not None
    assert resolution.status == "completed"
    assert resolution.tools_used == ["get_incident", "retrieve_runbooks"]
    assert resolution.prompt_name == "resolver"
    assert [step.outcome for step in steps] == ["succeeded", "succeeded", "completed"]
    assert "raw-super-secret" not in str([step.result_summary for step in steps])
    assert processed is not None
    assert processed.processing_result["phase"] == 5
    assert provider.calls == 4


def test_unknown_tool_is_rejected_then_repaired(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    provider = ScriptedProvider(
        [
            tool_plan("get_incident", {"incident_id": incident.id}),
            tool_plan(
                "retrieve_runbooks",
                {"query": "JWT expired", "classification": "authentication_error"},
            ),
            tool_plan("run_shell", {}),
            final_plan(),
            final_response(),
        ]
    )

    outcome = workflow_for(session_factory, provider).resolve(event_for(incident.id))

    assert outcome.value == "completed"
    with session_factory() as session:
        steps = list(
            session.scalars(
                select(AgentStepRecord).order_by(AgentStepRecord.step_number)
            )
        )
    assert steps[2].tool_name == "run_shell"
    assert steps[2].outcome == "tool_request_rejected"
    assert steps[2].sanitized_arguments == {}


def test_provider_outage_retries_without_processed_marker(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    provider = ScriptedProvider(
        [ProviderUnavailableError("Ollama offline"), *success_script(incident.id)]
    )
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, provider),
    )
    event = event_for(incident.id)

    with pytest.raises(RetryableProcessingError):
        processor.process(event)
    with session_factory() as session:
        execution = session.scalar(select(AgentExecutionRecord))
        assert execution is not None and execution.status == "retryable"
        assert (
            session.scalar(select(func.count()).select_from(ProcessedEventRecord)) == 0
        )

    assert processor.process(event) is ProcessingOutcome.PROCESSED
    with session_factory() as session:
        execution = session.scalar(select(AgentExecutionRecord))
    assert execution is not None
    assert execution.retry_count == 1
    assert execution.status == "awaiting_review"


def test_invalid_citation_is_repaired_locally(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    script = success_script(incident.id)
    script[-1:] = [final_response(bad_citation=True), final_response()]
    provider = ScriptedProvider(script)

    assert (
        workflow_for(session_factory, provider).resolve(event_for(incident.id)).value
        == "completed"
    )
    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
    assert resolution is not None
    assert resolution.cited_sources == [
        {"source_id": "runbook-jwt-authentication", "chunk_id": 7}
    ]


def test_insufficient_evidence_forces_uncertainty_and_escalation(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    script = success_script(incident.id)
    script[-1] = final_response(insufficient=True)
    provider = ScriptedProvider(script)

    workflow_for(session_factory, provider, empty_retrieval=True).resolve(
        event_for(incident.id)
    )

    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
    assert resolution is not None
    assert resolution.confidence == 0.5
    assert resolution.escalation_required is True
    assert resolution.human_review_recommended is True
    assert resolution.limitations


def test_restart_after_final_commit_does_not_repeat_model_calls(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    event = event_for(incident.id)
    first = ScriptedProvider(success_script(incident.id))
    assert workflow_for(session_factory, first).resolve(event).value == "completed"

    restarted = ScriptedProvider([])
    assert workflow_for(session_factory, restarted).resolve(event).value == "completed"
    assert restarted.calls == 0


def test_second_retrieval_is_rejected_even_with_different_query(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    provider = ScriptedProvider(
        [
            tool_plan("get_incident", {"incident_id": incident.id}),
            tool_plan("retrieve_runbooks", {"query": "JWT expired"}),
            tool_plan("retrieve_runbooks", {"query": "different JWT query"}),
            final_plan(),
            final_response(),
        ]
    )

    outcome = workflow_for(session_factory, provider).resolve(event_for(incident.id))

    assert outcome.value == "completed"
    with session_factory() as session:
        steps = list(
            session.scalars(
                select(AgentStepRecord).order_by(AgentStepRecord.step_number)
            )
        )
    assert steps[2].outcome == "repeated_tool_call"


def test_database_pool_incident_uses_grounded_final_after_bounded_planner_repairs(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_database_pool_incident(session_factory)
    invalid_shape = {
        "goal": "Resolve the database connection incident.",
        "next_action": "tool",
        "tool_arguments": {"incident_id": incident.id},
        "reason_summary": "Additional evidence may help.",
        "expected_evidence": "Database pool evidence.",
    }
    provider = ScriptedProvider(
        [
            tool_plan("get_incident", {"incident_id": incident.id}),
            tool_plan(
                "retrieve_runbooks",
                {
                    "query": "PostgreSQL connection pool exhausted pool timeout",
                    "classification": "database_connection_error",
                    "top_k": 4,
                },
            ),
            *[dict(invalid_shape) for _ in range(3)],
            final_response(source_id="runbook-postgresql-connectivity")
            | {
                "summary": "The billing API exhausted its PostgreSQL pool.",
                "root_cause": "Connections remained occupied until pool timeout.",
                "recommended_actions": [
                    "Inspect pool utilization and long-running transactions."
                ],
                "evidence_summary": (
                    "Incident metadata and the PostgreSQL runbook agree."
                ),
            },
        ]
    )

    outcome = workflow_for(
        session_factory,
        provider,
        retrieval_category="database",
    ).resolve(database_event_for(incident.id))

    assert outcome.value == "completed"
    with session_factory() as session:
        execution = session.scalar(select(AgentExecutionRecord))
        resolution = session.scalar(select(AIResolutionRecord))
        steps = list(
            session.scalars(
                select(AgentStepRecord).order_by(AgentStepRecord.step_number)
            )
        )
    assert execution is not None
    assert execution.status == "awaiting_review"
    assert execution.retry_count == 0
    assert execution.model_call_count == 6
    assert resolution is not None
    assert resolution.status == "completed"
    assert resolution.cited_sources == [
        {"source_id": "runbook-postgresql-connectivity", "chunk_id": 7}
    ]
    assert [step.outcome for step in steps] == ["succeeded", "succeeded", "completed"]
    assert provider.calls == 6


def test_planner_normalizes_only_unambiguous_structured_fields() -> None:
    final = PlannerDecision.model_validate(
        {
            "goal": "Produce the grounded response.",
            "next_action": "final-answer",
            "tool_name": "",
            "tool_arguments": {},
            "reason_summary": "Required evidence is present.",
            "expected_evidence": "A cited response.",
        }
    )
    assert final.next_action == "final"
    assert final.tool_name is None
    assert final.tool_arguments is None

    required = RequiredIncidentDecision.model_validate(
        {
            "goal": "Load the incident.",
            "arguments": {"incident_id": 7},
            "reason_summary": "Incident evidence is mandatory.",
            "expected_evidence": "Sanitized incident metadata.",
        }
    )
    assert required.next_action == "tool"
    assert required.tool_name == "get_incident"
    assert required.tool_arguments.incident_id == 7

    retrieval = RequiredRetrievalDecision.model_validate(
        {
            "goal": "Load a runbook.",
            "next_action": "tool-call",
            "tool": "retrieve_runbooks",
            "arguments": {"query": "database pool timeout"},
            "reason_summary": "Runbook evidence is mandatory.",
            "expected_evidence": "Relevant approved runbook chunks.",
        }
    )
    assert retrieval.tool_name == "retrieve_runbooks"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "goal": "Choose another tool.",
            "next_action": "tool",
            "tool_arguments": {"incident_id": 18},
            "reason_summary": "More evidence may be useful.",
            "expected_evidence": "Additional evidence.",
        },
        {
            "goal": "Choose an action.",
            "next_action": "execute",
            "tool_name": None,
            "tool_arguments": None,
            "reason_summary": "The action is not a recognized planner literal.",
            "expected_evidence": "Unknown.",
        },
        {
            "goal": "Conflicting tool fields.",
            "next_action": "tool",
            "tool_name": "get_incident",
            "tool": "retrieve_runbooks",
            "tool_arguments": {"incident_id": 18},
            "reason_summary": "The aliases conflict.",
            "expected_evidence": "Unknown.",
        },
    ],
)
def test_planner_rejects_ambiguous_or_unsafe_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PlannerDecision.model_validate(payload)


def test_planner_models_reject_extra_fields() -> None:
    payload = final_plan() | {"hidden_reasoning": "not allowed"}
    with pytest.raises(ValueError):
        PlannerDecision.model_validate(payload)
    with pytest.raises(ValueError):
        AgentFinalResponse.model_validate(final_response() | {"chain_of_thought": "no"})
