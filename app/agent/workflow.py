"""Bounded, durable, auditable Phase 5 support-agent workflow."""

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.errors import (
    AgentBudgetExceededError,
    AgentValidationError,
    ToolExecutionError,
    ToolTimeoutError,
)
from app.agent.models import (
    AgentFinalResponse,
    AgentStatus,
    EvidenceReference,
    PlannerDecision,
    ToolRequest,
    ToolResult,
    normalize_planner_payload,
)
from app.agent.sanitization import safe_error, sanitize_value
from app.agent.tools import (
    GetIncidentInput,
    RetrieveRunbooksInput,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
)
from app.ai.models import AIResolutionStatus
from app.ai.prompt_registry import PromptRegistry
from app.ai.providers.base import (
    LLMProvider,
    ProviderResponseError,
    ProviderUnavailableError,
    StructuredOutputValidationError,
)
from app.ai.workflow import (
    ResolutionWorkflowOutcome,
    RetryableResolutionError,
)
from app.db.models import (
    AgentExecutionRecord,
    AgentStepRecord,
    AIResolutionRecord,
    IncidentRecord,
)
from app.events.models import IncidentCreatedEvent
from app.knowledge.retrieval import KnowledgeRetriever
from app.observability.context import set_current_span_attributes
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span

logger = logging.getLogger(__name__)
StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def _normalize_required_tool_payload(value: object, tool_name: str) -> object:
    """Fill only literals already mandated by the server-owned execution state."""
    normalized = normalize_planner_payload(value)
    if not isinstance(normalized, dict):
        return normalized
    payload = dict(normalized)
    if payload.get("next_action") is None:
        payload["next_action"] = "tool"
    if payload.get("next_action") == "tool" and payload.get("tool_name") in {
        None,
        "",
    }:
        payload["tool_name"] = tool_name
    return normalize_planner_payload(payload)


class RequiredIncidentDecision(BaseModel):
    """Planner shape while server policy requires incident evidence first."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    next_action: Literal["tool"]
    tool_name: Literal["get_incident"]
    tool_arguments: GetIncidentInput
    reason_summary: str = Field(min_length=1, max_length=500)
    expected_evidence: str = Field(min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def normalize_required_literals(cls, value: object) -> object:
        return _normalize_required_tool_payload(value, "get_incident")


class RequiredRetrievalDecision(BaseModel):
    """Planner shape while server policy requires one runbook search."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    next_action: Literal["tool"]
    tool_name: Literal["retrieve_runbooks"]
    tool_arguments: RetrieveRunbooksInput
    reason_summary: str = Field(min_length=1, max_length=500)
    expected_evidence: str = Field(min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def normalize_required_literals(cls, value: object) -> object:
        return _normalize_required_tool_payload(value, "retrieve_runbooks")


@dataclass(frozen=True)
class AgentLimits:
    """Server-owned hard limits for one execution."""

    max_steps: int
    max_tool_calls: int
    max_repeated_tool_calls: int
    max_retrieval_chunks: int
    max_duration_seconds: float
    tool_timeout_seconds: float
    model_retries: int
    repair_attempts: int


def _json(value: object, *, limit: int = 30_000) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return encoded[:limit]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ControlledAgentWorkflow:
    """Plan and execute only approved evidence tools before a reviewed final result."""

    phase = 5

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        retriever: KnowledgeRetriever,
        llm_provider: LLMProvider,
        limits: AgentLimits,
        *,
        planner_prompt_version: str = "v2",
        resolver_prompt_version: str = "v1",
        prompt_registry: PromptRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.retriever = retriever
        self.llm_provider = llm_provider
        self.limits = limits
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.planner_prompt = self.prompt_registry.load(
            "planner", planner_prompt_version
        )
        self.resolver_prompt = self.prompt_registry.load(
            "resolver", resolver_prompt_version
        )
        self.prompt_registry.load("evidence_summary", "v1")
        self.tool_registry = tool_registry or ToolRegistry()
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            timeout_seconds=limits.tool_timeout_seconds,
            max_tool_calls=limits.max_tool_calls,
        )
        capabilities = getattr(llm_provider, "capabilities", None)
        if capabilities is not None and not capabilities.structured_output:
            raise ValueError(
                "The configured provider does not support structured output"
            )

    def resolve(self, event: IncidentCreatedEvent) -> ResolutionWorkflowOutcome:
        """Create the bounded execution span around a durable/resumable run."""
        with start_span(
            "agent execution",
            attributes={
                "event.id": str(event.event_id),
                "incident.id": event.incident_id,
                "incident.classification": event.classification.value,
                "incident.severity": event.severity.value,
                "agent.provider": self.llm_provider.provider_name,
                "agent.model": self.llm_provider.model_name,
            },
        ) as execution_span:
            try:
                return self._resolve(event)
            except Exception as exc:
                mark_span_error(execution_span, exc)
                raise

    def _resolve(self, event: IncidentCreatedEvent) -> ResolutionWorkflowOutcome:
        """Run or resume an execution and persist final output before Kafka commit."""
        _incident, execution_id, terminal = self._claim(event)
        set_current_span_attributes(
            {
                "agent.execution_id": execution_id,
                "event.id": str(event.event_id),
                "incident.id": event.incident_id,
                "incident.classification": event.classification.value,
                "incident.severity": event.severity.value,
            }
        )
        if terminal is not None:
            return terminal
        started = time.perf_counter()
        last_error = "none"
        repair_count = 0

        try:
            step_records = self._load_steps(execution_id)
            previous_results = self._restore_results(step_records)
            tool_calls = sum(1 for step in step_records if step.outcome == "succeeded")
            signatures = Counter(
                self._signature(step.tool_name, step.sanitized_arguments or {})
                for step in step_records
                if step.tool_name and step.outcome == "succeeded"
            )
            context = ToolContext(
                incident_id=event.incident_id,
                session_factory=self.session_factory,
                retriever=self.retriever,
                classification=event.classification.value,
                max_retrieval_chunks=self.limits.max_retrieval_chunks,
                previous_results=previous_results,
            )

            while True:
                self._check_time_and_step_budgets(
                    execution_id, started, len(step_records)
                )
                required_tools = self._missing_required_evidence(step_records)
                planner_prompt = self._build_planner_prompt(
                    event,
                    step_records,
                    last_error,
                    required_tool=required_tools[0] if required_tools else None,
                )
                try:
                    decision = self._plan(
                        execution_id,
                        planner_prompt,
                        required_tool=required_tools[0] if required_tools else None,
                    )
                except ProviderResponseError as exc:
                    repair_count += 1
                    last_error = safe_error(exc)
                    if repair_count > self.limits.repair_attempts:
                        if required_tools or not isinstance(
                            exc, StructuredOutputValidationError
                        ):
                            raise AgentValidationError(
                                "Planner output remained invalid after repair attempts"
                            ) from exc
                        decision = self._grounded_final_fallback(
                            execution_id,
                            event,
                            repair_count=repair_count,
                        )
                    else:
                        continue

                if decision.next_action == "tool":
                    request = ToolRequest(
                        tool_name=decision.tool_name or "",
                        arguments=decision.tool_arguments or {},
                    )
                    signature = self._signature(request.tool_name, request.arguments)
                    repeated_retrieval = (
                        request.tool_name == "retrieve_runbooks"
                        and any(
                            step.tool_name == "retrieve_runbooks"
                            and step.outcome == "succeeded"
                            for step in step_records
                        )
                    )
                    if (
                        signatures[signature] >= self.limits.max_repeated_tool_calls
                        or repeated_retrieval
                    ):
                        step_records.append(
                            self._persist_rejected_step(
                                execution_id,
                                len(step_records) + 1,
                                decision,
                                request,
                                "repeated_tool_call",
                            )
                        )
                        repair_count += 1
                        last_error = "That exact tool call already ran; choose new evidence or final."
                        if repair_count > self.limits.repair_attempts:
                            raise AgentValidationError(
                                "Repeated tool-call guardrail triggered"
                            )
                        continue
                    self._set_status(execution_id, AgentStatus.WAITING_FOR_TOOL)
                    try:
                        result = self.tool_executor.execute(
                            request,
                            context,
                            current_tool_calls=tool_calls,
                        )
                    except (ToolExecutionError, AgentValidationError) as exc:
                        if isinstance(exc, ToolTimeoutError):
                            raise
                        step_records.append(
                            self._persist_rejected_step(
                                execution_id,
                                len(step_records) + 1,
                                decision,
                                request,
                                "tool_request_rejected",
                            )
                        )
                        repair_count += 1
                        last_error = safe_error(exc)
                        if repair_count > self.limits.repair_attempts:
                            raise AgentValidationError(
                                "Tool request remained invalid after repair attempts"
                            ) from exc
                        self._set_status(execution_id, AgentStatus.RUNNING)
                        continue

                    record = self._persist_tool_step(
                        execution_id,
                        len(step_records) + 1,
                        decision,
                        request,
                        result,
                    )
                    step_records.append(record)
                    context.previous_results[record.step_number] = result
                    previous_results[record.step_number] = result
                    tool_calls += 1
                    signatures[signature] += 1
                    repair_count = 0
                    last_error = "none"
                    continue

                missing = self._missing_required_evidence(step_records)
                if missing:
                    step_records.append(
                        self._persist_rejected_step(
                            execution_id,
                            len(step_records) + 1,
                            decision,
                            None,
                            "final_missing_required_evidence",
                        )
                    )
                    repair_count += 1
                    last_error = "Before final, call required tools: " + ", ".join(
                        missing
                    )
                    if repair_count > self.limits.repair_attempts:
                        raise AgentValidationError(
                            "Planner attempted final without required evidence"
                        )
                    continue

                final = self._generate_final(
                    execution_id, event, step_records, previous_results
                )
                self._persist_final(
                    execution_id,
                    event,
                    decision,
                    final,
                    step_records,
                    previous_results,
                )
                logger.info(
                    "controlled_agent_completed",
                    extra={
                        "execution_id": execution_id,
                        "event_id": str(event.event_id),
                        "incident_id": event.incident_id,
                        "status": AgentStatus.AWAITING_REVIEW.value,
                        "step_count": len(step_records) + 1,
                        "tool_call_count": tool_calls,
                        "citation_count": len(final.cited_sources),
                    },
                )
                self._record_execution_metrics(
                    status=AgentStatus.AWAITING_REVIEW.value,
                    classification=event.classification.value,
                    started=started,
                )
                return ResolutionWorkflowOutcome.COMPLETED
        except (ProviderUnavailableError, ToolTimeoutError, SQLAlchemyError) as exc:
            self._record_failure(execution_id, exc, retryable=True)
            self._record_execution_metrics(
                status=AgentStatus.RETRYABLE.value,
                classification=event.classification.value,
                started=started,
                failure=True,
                retry=True,
            )
            raise RetryableResolutionError(safe_error(exc)) from exc
        except (AgentValidationError, ToolExecutionError) as exc:
            terminal_failure = self._record_failure(execution_id, exc, retryable=False)
            status = (
                AgentStatus.FAILED.value
                if terminal_failure
                else AgentStatus.RETRYABLE.value
            )
            self._record_execution_metrics(
                status=status,
                classification=event.classification.value,
                started=started,
                failure=True,
                retry=not terminal_failure,
            )
            if terminal_failure:
                return ResolutionWorkflowOutcome.FAILED
            raise RetryableResolutionError(safe_error(exc)) from exc
        except AgentBudgetExceededError as exc:
            self._record_terminal_failure(execution_id, exc)
            self._record_execution_metrics(
                status=AgentStatus.FAILED.value,
                classification=event.classification.value,
                started=started,
                failure=True,
            )
            return ResolutionWorkflowOutcome.FAILED

    def _claim(
        self, event: IncidentCreatedEvent
    ) -> tuple[IncidentRecord, str, ResolutionWorkflowOutcome | None]:
        with start_span(
            "agent execution load or resume",
            attributes={
                "event.id": str(event.event_id),
                "incident.id": event.incident_id,
            },
        ):
            return self._claim_impl(event)

    def _claim_impl(
        self, event: IncidentCreatedEvent
    ) -> tuple[IncidentRecord, str, ResolutionWorkflowOutcome | None]:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            incident = session.get(IncidentRecord, event.incident_id)
            if incident is None:
                raise RetryableResolutionError(
                    f"Incident {event.incident_id} is not visible in PostgreSQL"
                )
            execution = session.scalar(
                select(AgentExecutionRecord)
                .where(AgentExecutionRecord.incident_id == event.incident_id)
                .with_for_update()
            )
            if execution is not None and execution.status in {
                AgentStatus.AWAITING_REVIEW,
                AgentStatus.APPROVED,
                AgentStatus.REJECTED,
                AgentStatus.COMPLETED,
            }:
                session.expunge(incident)
                return (
                    incident,
                    execution.execution_id,
                    ResolutionWorkflowOutcome.COMPLETED,
                )
            if execution is not None and execution.status in {
                AgentStatus.FAILED,
                AgentStatus.CANCELLED,
            }:
                session.expunge(incident)
                return (
                    incident,
                    execution.execution_id,
                    ResolutionWorkflowOutcome.FAILED,
                )

            if execution is None:
                execution = AgentExecutionRecord(
                    execution_id=str(uuid4()),
                    incident_id=event.incident_id,
                    event_id=str(event.event_id),
                    status=AgentStatus.RUNNING,
                    provider=self.llm_provider.provider_name,
                    model=self.llm_provider.model_name,
                    planner_prompt_name=self.planner_prompt.name,
                    planner_prompt_version=self.planner_prompt.version,
                    resolver_prompt_name=self.resolver_prompt.name,
                    resolver_prompt_version=self.resolver_prompt.version,
                    started_at=now,
                    updated_at=now,
                )
                session.add(execution)
            else:
                execution.status = AgentStatus.RUNNING
                execution.retry_count += 1
                execution.error_type = None
                execution.error_message_safe = None
                execution.updated_at = now

            resolution = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == event.incident_id
                )
            )
            if resolution is None:
                resolution = AIResolutionRecord(
                    incident_id=event.incident_id,
                    event_id=str(event.event_id),
                    status=AIResolutionStatus.PROCESSING,
                    attempt_count=1,
                    llm_provider=self.llm_provider.provider_name,
                    llm_model=self.llm_provider.model_name,
                    embedding_provider=self.retriever.embedding_provider.provider_name,
                    embedding_model=self.retriever.embedding_provider.model_name,
                    prompt_name=self.resolver_prompt.name,
                    prompt_version=self.resolver_prompt.version,
                    processing_started_at=now,
                    updated_at=now,
                )
                session.add(resolution)
            else:
                resolution.status = AIResolutionStatus.PROCESSING
                resolution.attempt_count += 1
                resolution.processing_started_at = now
                resolution.updated_at = now
                resolution.error_message = None
            session.commit()
            execution_id = execution.execution_id
            session.expunge(incident)
            return incident, execution_id, None

    def _load_steps(self, execution_id: str) -> list[AgentStepRecord]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(AgentStepRecord)
                    .where(AgentStepRecord.execution_id == execution_id)
                    .order_by(AgentStepRecord.step_number)
                )
            )

    @staticmethod
    def _restore_results(records: list[AgentStepRecord]) -> dict[int, ToolResult]:
        results: dict[int, ToolResult] = {}
        for record in records:
            if record.action_type != "tool" or record.outcome != "succeeded":
                continue
            if record.result_summary is not None:
                results[record.step_number] = ToolResult.model_validate(
                    record.result_summary
                )
        return results

    def _build_planner_prompt(
        self,
        event: IncidentCreatedEvent,
        steps: list[AgentStepRecord],
        last_error: str,
        *,
        required_tool: str | None,
    ) -> str:
        history = [
            {
                "step": step.step_number,
                "action": step.action_type,
                "tool": step.tool_name,
                "arguments": step.sanitized_arguments,
                "result": step.result_summary,
                "outcome": step.outcome,
            }
            for step in steps
        ]
        prompt = self.planner_prompt.render(
            {
                "tool_catalog": _json(self.tool_registry.catalog(), limit=18_000),
                "limits": _json(
                    {
                        "max_steps": self.limits.max_steps,
                        "max_tool_calls": self.limits.max_tool_calls,
                        "max_repeated_tool_calls": self.limits.max_repeated_tool_calls,
                        "remaining_steps": self.limits.max_steps - len(steps),
                    }
                ),
                "incident_event": event.model_dump_json(),
                "step_history": _json(history, limit=24_000),
                "last_error": last_error[:1_000],
            }
        )
        if required_tool:
            prompt += (
                "\nSERVER POLICY: The next action must be the typed tool "
                f"{required_tool}. Final output is not eligible yet."
            )
        return prompt

    def _plan(
        self,
        execution_id: str,
        prompt: str,
        *,
        required_tool: str | None,
    ) -> PlannerDecision:
        """Use a state-specific schema so mandatory evidence cannot be skipped."""
        if required_tool == "get_incident":
            raw, _ = self._model_call(execution_id, prompt, RequiredIncidentDecision)
        elif required_tool == "retrieve_runbooks":
            raw, _ = self._model_call(execution_id, prompt, RequiredRetrievalDecision)
        else:
            raw, _ = self._model_call(execution_id, prompt, PlannerDecision)
        if isinstance(raw, PlannerDecision):
            return raw
        payload = raw.model_dump(mode="json")
        return PlannerDecision.model_validate(payload)

    @staticmethod
    def _grounded_final_fallback(
        execution_id: str,
        event: IncidentCreatedEvent,
        *,
        repair_count: int,
    ) -> PlannerDecision:
        """Finish safely after bounded schema repairs and mandatory evidence.

        The caller invokes this only when both required evidence tools succeeded.
        It never infers or executes a missing/unknown tool name.
        """
        attributes = {
            "agent.execution_id": execution_id,
            "incident.id": event.incident_id,
            "incident.classification": event.classification.value,
            "agent.repair_count": repair_count,
        }
        with start_span("agent planner deterministic fallback", attributes=attributes):
            decision = PlannerDecision.model_validate(
                {
                    "goal": "Produce a grounded resolution from validated evidence.",
                    "next_action": "final",
                    "tool_name": None,
                    "tool_arguments": None,
                    "reason_summary": (
                        "Required incident and runbook evidence is available."
                    ),
                    "expected_evidence": (
                        "A cited resolution using only the validated evidence."
                    ),
                }
            )
        get_metrics().count(
            "agent_planner_fallbacks",
            attributes={"classification": event.classification.value},
        )
        logger.warning(
            "controlled_agent_planner_fallback",
            extra={
                "execution_id": execution_id,
                "event_id": str(event.event_id),
                "incident_id": event.incident_id,
                "classification": event.classification.value,
                "repair_count": repair_count,
            },
        )
        return decision

    def _generate_final(
        self,
        execution_id: str,
        event: IncidentCreatedEvent,
        steps: list[AgentStepRecord],
        results: dict[int, ToolResult],
    ) -> AgentFinalResponse:
        with start_span("rag context construction"):
            evidence = [
                {
                    "step": number,
                    "tool": result.tool_name,
                    "summary": result.summary,
                    "data": result.data,
                    "evidence_references": [
                        reference.model_dump(mode="json")
                        for reference in result.evidence_references
                    ],
                }
                for number, result in results.items()
            ]
            references = self._evidence_references(results)
            citations = [
                {
                    "source_id": reference.source_id,
                    "chunk_id": reference.chunk_id,
                }
                for reference in references
                if reference.evidence_type == "runbook"
                and reference.source_id
                and reference.chunk_id
            ]
            actual_tools = list(
                dict.fromkeys(
                    step.tool_name
                    for step in steps
                    if step.tool_name and step.outcome == "succeeded"
                )
            )
        prompt = self.resolver_prompt.render(
            {
                "incident_event": event.model_dump_json(),
                "evidence": _json(evidence, limit=28_000),
                "citations": _json(citations),
                "tools_used": _json(actual_tools),
            }
        )
        last_error: Exception | None = None
        for _ in range(self.limits.repair_attempts + 1):
            try:
                final, _ = self._model_call(execution_id, prompt, AgentFinalResponse)
                with start_span("agent final validation"):
                    return self._validate_final(final, actual_tools, references)
            except (ProviderResponseError, AgentValidationError) as exc:
                last_error = exc
                logger.warning(
                    "controlled_agent_final_rejected",
                    extra={
                        "execution_id": execution_id,
                        "error_type": type(exc).__name__,
                        "error": safe_error(exc),
                    },
                )
                prompt += (
                    "\nLOCAL VALIDATION ERROR: "
                    + safe_error(exc)
                    + "\nReturn a corrected JSON object."
                )
        detail = safe_error(last_error) if last_error is not None else "unknown"
        raise AgentValidationError(
            f"Final response remained invalid after repair attempts: {detail}"
        ) from last_error

    @staticmethod
    def _validate_final(
        final: AgentFinalResponse,
        actual_tools: list[str],
        references: list[EvidenceReference],
    ) -> AgentFinalResponse:
        if len(final.tools_used) != len(set(final.tools_used)):
            raise AgentValidationError("Final tools_used contains duplicates")
        unknown_tools = set(final.tools_used) - set(actual_tools)
        if unknown_tools:
            raise AgentValidationError(
                f"Final output claimed tools that did not run: {sorted(unknown_tools)}"
            )
        allowed = {
            (reference.source_id, reference.chunk_id)
            for reference in references
            if reference.evidence_type == "runbook"
        }
        supplied = [
            (citation.source_id, citation.chunk_id) for citation in final.cited_sources
        ]
        if len(supplied) != len(set(supplied)):
            get_metrics().count("rag_invalid_citation")
            raise AgentValidationError("Final citations contain duplicates")
        invalid = [citation for citation in supplied if citation not in allowed]
        if invalid:
            get_metrics().count("rag_invalid_citation", len(invalid))
            raise AgentValidationError(
                f"Final citations are not in evidence: {invalid}"
            )
        if allowed and not supplied:
            get_metrics().count("rag_invalid_citation")
            raise AgentValidationError(
                "Final output omitted available runbook citations"
            )
        dangerous = (
            "rm -rf",
            "drop table",
            "delete from",
            "format c:",
            "i restarted",
            "we fixed",
        )
        for action in final.recommended_actions:
            if any(fragment in action.lower() for fragment in dangerous):
                raise AgentValidationError(
                    "Final action violated the remediation guardrail"
                )
        updates: dict[str, object] = {"tools_used": actual_tools}
        if not allowed:
            updates.update(
                {
                    "confidence": min(final.confidence, 0.5),
                    "escalation_required": True,
                    "human_review_recommended": True,
                    "limitations": final.limitations
                    or ["No citation-addressable runbook evidence was available."],
                }
            )
        return final.model_copy(update=updates)

    def _model_call(
        self,
        execution_id: str,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, float]:
        started = time.perf_counter()
        output_chars = 0
        operation = "resolver" if response_model is AgentFinalResponse else "planner"
        prompt_definition = (
            self.resolver_prompt if operation == "resolver" else self.planner_prompt
        )
        metric_attributes = {
            "provider": self.llm_provider.provider_name,
            "model": self.llm_provider.model_name,
            "operation": operation,
            "prompt_name": prompt_definition.name,
            "prompt_version": prompt_definition.version,
        }
        with start_span(
            f"agent {operation} model call",
            attributes={
                "agent.execution_id": execution_id,
                "agent.provider": self.llm_provider.provider_name,
                "agent.model": self.llm_provider.model_name,
                "agent.prompt_name": prompt_definition.name,
                "agent.prompt_version": prompt_definition.version,
            },
        ) as model_span:
            try:
                response = self.llm_provider.generate_structured(prompt, response_model)
                output_chars = len(response.model_dump_json())
                return response, (time.perf_counter() - started) * 1_000
            except Exception as exc:
                mark_span_error(model_span, exc)
                get_metrics().count(
                    "agent_model_failures",
                    attributes={
                        "provider": self.llm_provider.provider_name,
                        "model": self.llm_provider.model_name,
                        "operation": operation,
                    },
                )
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1_000
                get_metrics().count("agent_model_calls", attributes=metric_attributes)
                get_metrics().observe(
                    "agent_model_duration_seconds",
                    duration_ms / 1_000,
                    {
                        "provider": self.llm_provider.provider_name,
                        "model": self.llm_provider.model_name,
                        "operation": operation,
                    },
                )
                usage = sanitize_value(getattr(self.llm_provider, "last_usage", None))
                with (
                    start_span("agent model metadata persistence"),
                    self.session_factory() as session,
                ):
                    execution = session.scalar(
                        select(AgentExecutionRecord).where(
                            AgentExecutionRecord.execution_id == execution_id
                        )
                    )
                    if execution is not None:
                        execution.model_call_count += 1
                        execution.provider_duration_ms += duration_ms
                        execution.approximate_input_chars += len(prompt)
                        execution.approximate_output_chars += output_chars
                        execution.provider_usage = usage
                        execution.updated_at = datetime.now(UTC)
                        session.commit()

    def _persist_tool_step(
        self,
        execution_id: str,
        step_number: int,
        decision: PlannerDecision,
        request: ToolRequest,
        result: ToolResult,
    ) -> AgentStepRecord:
        with start_span(
            "agent step persistence",
            attributes={
                "agent.execution_id": execution_id,
                "agent.step_number": step_number,
                "agent.tool_name": request.tool_name,
            },
        ):
            record = self._persist_tool_step_impl(
                execution_id, step_number, decision, request, result
            )
        get_metrics().count(
            "agent_steps",
            attributes={"action_type": "tool", "outcome": "succeeded"},
        )
        with start_span("agent evidence accumulation"):
            retrieved = {
                reference.reference_id
                for reference in result.evidence_references
                if reference.evidence_type == "runbook"
            }
            if retrieved:
                get_metrics().count("agent_retrieved_chunks", len(retrieved))
        return record

    def _persist_tool_step_impl(
        self,
        execution_id: str,
        step_number: int,
        decision: PlannerDecision,
        request: ToolRequest,
        result: ToolResult,
    ) -> AgentStepRecord:
        sanitized_arguments = sanitize_value(request.arguments)
        sanitized_result = sanitize_value(result.model_dump(mode="json"))
        with self.session_factory() as session:
            record = AgentStepRecord(
                execution_id=execution_id,
                step_number=step_number,
                action_type="tool",
                tool_name=request.tool_name,
                sanitized_arguments=sanitized_arguments,
                result_summary=sanitized_result,
                evidence_references=[
                    item.model_dump(mode="json") for item in result.evidence_references
                ],
                reason_summary=decision.reason_summary,
                expected_evidence=decision.expected_evidence,
                outcome="succeeded",
                duration_ms=result.duration_ms,
            )
            session.add(record)
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if execution is None:
                raise SQLAlchemyError("Agent execution disappeared")
            execution.status = AgentStatus.RUNNING
            execution.step_count = step_number
            execution.tool_call_count += 1
            execution.tool_duration_ms += result.duration_ms
            if request.tool_name == "retrieve_runbooks":
                execution.retrieval_duration_ms += result.duration_ms
            existing_refs = {
                item.reference_id
                for item in result.evidence_references
                if item.evidence_type == "runbook"
            }
            execution.retrieved_chunk_count += len(existing_refs)
            execution.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def _persist_rejected_step(
        self,
        execution_id: str,
        step_number: int,
        decision: PlannerDecision,
        request: ToolRequest | None,
        outcome: str,
    ) -> AgentStepRecord:
        with self.session_factory() as session:
            record = AgentStepRecord(
                execution_id=execution_id,
                step_number=step_number,
                action_type=decision.next_action,
                tool_name=request.tool_name if request else None,
                sanitized_arguments=(
                    sanitize_value(request.arguments) if request else None
                ),
                result_summary=None,
                evidence_references=[],
                reason_summary=decision.reason_summary,
                expected_evidence=decision.expected_evidence,
                outcome=outcome,
                duration_ms=0,
            )
            session.add(record)
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if execution is None:
                raise SQLAlchemyError("Agent execution disappeared")
            execution.status = AgentStatus.RUNNING
            execution.step_count = step_number
            execution.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            get_metrics().count(
                "agent_steps",
                attributes={"action_type": decision.next_action, "outcome": outcome},
            )
            return record

    def _persist_final(
        self,
        execution_id: str,
        event: IncidentCreatedEvent,
        decision: PlannerDecision,
        final: AgentFinalResponse,
        steps: list[AgentStepRecord],
        results: dict[int, ToolResult],
    ) -> None:
        with start_span(
            "agent resolution persistence",
            attributes={
                "agent.execution_id": execution_id,
                "event.id": str(event.event_id),
                "incident.id": event.incident_id,
            },
        ):
            self._persist_final_impl(
                execution_id, event, decision, final, steps, results
            )
        get_metrics().count(
            "agent_steps",
            attributes={"action_type": "final", "outcome": "completed"},
        )

    def _persist_final_impl(
        self,
        execution_id: str,
        event: IncidentCreatedEvent,
        decision: PlannerDecision,
        final: AgentFinalResponse,
        steps: list[AgentStepRecord],
        results: dict[int, ToolResult],
    ) -> None:
        now = datetime.now(UTC)
        references = self._evidence_references(results)
        context = self._retrieved_context(results)
        with self.session_factory() as session:
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            resolution = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == event.incident_id
                )
            )
            if execution is None or resolution is None:
                raise SQLAlchemyError("Agent durable state disappeared")
            step_number = len(steps) + 1
            session.add(
                AgentStepRecord(
                    execution_id=execution_id,
                    step_number=step_number,
                    action_type="final",
                    tool_name=None,
                    sanitized_arguments=None,
                    result_summary=sanitize_value(final.model_dump(mode="json")),
                    evidence_references=[
                        item.model_dump(mode="json") for item in references
                    ],
                    reason_summary=decision.reason_summary,
                    expected_evidence=decision.expected_evidence,
                    outcome="completed",
                    duration_ms=0,
                )
            )
            resolution.status = AIResolutionStatus.COMPLETED
            resolution.summary = final.summary
            resolution.root_cause = final.root_cause
            resolution.recommended_actions = final.recommended_actions
            resolution.confidence = final.confidence
            resolution.cited_sources = [
                item.model_dump(mode="json") for item in final.cited_sources
            ]
            resolution.retrieved_context = context
            resolution.evidence_summary = final.evidence_summary
            resolution.tools_used = final.tools_used
            resolution.limitations = final.limitations
            resolution.escalation_required = final.escalation_required
            resolution.human_review_recommended = final.human_review_recommended
            resolution.prompt_name = self.resolver_prompt.name
            resolution.prompt_version = self.resolver_prompt.version
            resolution.error_message = None
            resolution.completed_at = now
            resolution.updated_at = now
            execution.status = AgentStatus.AWAITING_REVIEW
            execution.step_count = step_number
            execution.completed_at = now
            execution.total_duration_ms = max(
                0, (now - _aware(execution.created_at)).total_seconds() * 1_000
            )
            execution.error_type = None
            execution.error_message_safe = None
            execution.updated_at = now
            session.commit()

    @staticmethod
    def _record_execution_metrics(
        *,
        status: str,
        classification: str,
        started: float,
        failure: bool = False,
        retry: bool = False,
    ) -> None:
        attributes = {"status": status, "classification": classification}
        get_metrics().count("agent_executions", attributes=attributes)
        get_metrics().observe(
            "agent_execution_duration_seconds",
            time.perf_counter() - started,
            attributes,
        )
        if failure:
            get_metrics().count("agent_execution_failures", attributes=attributes)
        if retry:
            get_metrics().count(
                "agent_execution_retries",
                attributes={"classification": classification},
            )

    def _record_failure(
        self, execution_id: str, error: Exception, *, retryable: bool
    ) -> bool:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if execution is None:
                raise RetryableResolutionError("Agent execution state disappeared")
            terminal = not retryable and (
                execution.retry_count + 1 >= self.limits.model_retries
            )
            execution.status = AgentStatus.FAILED if terminal else AgentStatus.RETRYABLE
            execution.error_type = type(error).__name__[:120]
            execution.error_message_safe = safe_error(error)
            execution.updated_at = now
            resolution = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == execution.incident_id
                )
            )
            if resolution is not None:
                resolution.status = (
                    AIResolutionStatus.FAILED
                    if terminal
                    else AIResolutionStatus.RETRYABLE
                )
                resolution.error_message = safe_error(error)
                resolution.updated_at = now
            session.commit()
        logger.warning(
            "controlled_agent_failed",
            extra={
                "execution_id": execution_id,
                "status": (
                    AgentStatus.FAILED.value
                    if terminal
                    else AgentStatus.RETRYABLE.value
                ),
                "error_type": type(error).__name__,
                "error": safe_error(error),
            },
        )
        return terminal

    def _record_terminal_failure(self, execution_id: str, error: Exception) -> None:
        with self.session_factory() as session:
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if execution is None:
                return
            execution.retry_count = max(
                execution.retry_count, self.limits.model_retries - 1
            )
            session.commit()
        self._record_failure(execution_id, error, retryable=False)

    def _set_status(self, execution_id: str, status: AgentStatus) -> None:
        with self.session_factory() as session:
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if execution is None:
                raise SQLAlchemyError("Agent execution disappeared")
            execution.status = status
            execution.updated_at = datetime.now(UTC)
            session.commit()

    def _check_time_and_step_budgets(
        self, execution_id: str, started: float, step_count: int
    ) -> None:
        if step_count >= self.limits.max_steps:
            raise AgentBudgetExceededError("Maximum agent-step budget reached")
        if time.perf_counter() - started >= self.limits.max_duration_seconds:
            raise AgentBudgetExceededError("Maximum agent duration reached")
        with self.session_factory() as session:
            execution = session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.execution_id == execution_id
                )
            )
            if (
                execution is not None
                and execution.tool_call_count >= self.limits.max_tool_calls
            ):
                called = {
                    step.tool_name
                    for step in self._load_steps(execution_id)
                    if step.outcome == "succeeded"
                }
                if {"get_incident", "retrieve_runbooks"} - called:
                    raise AgentBudgetExceededError(
                        "Tool budget exhausted before required evidence was gathered"
                    )
            if execution is not None:
                total_elapsed = datetime.now(UTC) - _aware(execution.created_at)
                if total_elapsed.total_seconds() >= self.limits.max_duration_seconds:
                    raise AgentBudgetExceededError(
                        "Maximum total agent execution duration reached"
                    )

    @staticmethod
    def _signature(tool_name: str | None, arguments: dict[str, object]) -> str:
        return f"{tool_name}:{_json(sanitize_value(arguments))}"

    @staticmethod
    def _missing_required_evidence(steps: list[AgentStepRecord]) -> list[str]:
        called = {
            step.tool_name
            for step in steps
            if step.outcome == "succeeded" and step.tool_name
        }
        return [
            name for name in ("get_incident", "retrieve_runbooks") if name not in called
        ]

    @staticmethod
    def _evidence_references(results: dict[int, ToolResult]) -> list[EvidenceReference]:
        unique: dict[tuple[object, ...], EvidenceReference] = {}
        for result in results.values():
            for reference in result.evidence_references:
                key = (
                    reference.evidence_type,
                    reference.reference_id,
                    reference.source_id,
                    reference.chunk_id,
                )
                unique[key] = reference
        return list(unique.values())

    @staticmethod
    def _retrieved_context(results: dict[int, ToolResult]) -> list[dict[str, object]]:
        chunks: dict[tuple[object, object], dict[str, object]] = {}
        for result in results.values():
            raw_chunks = result.data.get("chunks")
            if not isinstance(raw_chunks, list):
                continue
            for item in raw_chunks:
                if not isinstance(item, dict):
                    continue
                source_id = item.get("source_id")
                chunk_id = item.get("chunk_id")
                if source_id is not None and chunk_id is not None:
                    chunks[(source_id, chunk_id)] = item
        return list(chunks.values())
