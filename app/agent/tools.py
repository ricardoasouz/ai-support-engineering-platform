"""Typed, read-only tools exposed to the controlled support agent."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.errors import (
    AgentBudgetExceededError,
    ToolExecutionError,
    ToolTimeoutError,
    UnauthorizedToolArgumentsError,
    UnknownToolError,
)
from app.agent.models import EvidenceReference, ToolRequest, ToolResult
from app.agent.sanitization import sanitize_text, sanitize_value
from app.ai.providers.base import ProviderUnavailableError
from app.db.models import AgentExecutionRecord, AIResolutionRecord, IncidentRecord
from app.knowledge.retrieval import KnowledgeRetriever

MAX_TOOL_ARGUMENT_BYTES = 8_000
MAX_TOOL_RESULT_BYTES = 24_000


class StrictToolInput(BaseModel):
    """Base for strict arguments; coercion remains limited to Pydantic defaults."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GetIncidentInput(StrictToolInput):
    incident_id: int = Field(gt=0)


class RetrieveRunbooksInput(StrictToolInput):
    query: str = Field(min_length=3, max_length=2_000)
    classification: str | None = Field(default=None, max_length=100)
    top_k: int = Field(default=4, ge=1, le=8)


class IncidentScopedListInput(StrictToolInput):
    incident_id: int = Field(gt=0)
    limit: int = Field(default=5, ge=1, le=10)


class ResolutionContextInput(StrictToolInput):
    incident_id: int = Field(gt=0)


class SummarizeEvidenceInput(StrictToolInput):
    step_numbers: list[int] = Field(min_length=1, max_length=12)


class ToolOutput(BaseModel):
    """Internal typed return value before the executor adds runtime metadata."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    data: dict[str, object] = Field(default_factory=dict)
    evidence_references: list[EvidenceReference] = Field(
        default_factory=list, max_length=20
    )


@dataclass
class ToolContext:
    """Server-owned dependencies and state unavailable for planner mutation."""

    incident_id: int
    session_factory: sessionmaker[Session]
    retriever: KnowledgeRetriever
    classification: str | None = None
    max_retrieval_chunks: int = 4
    previous_results: dict[int, ToolResult] = field(default_factory=dict)


class AgentTool(Protocol):
    """Contract implemented only by server-registered read-only tools."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[StrictToolInput]]

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        """Execute with locally validated arguments and server-owned context."""


def _bounded_text(value: str | None, limit: int = 800) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:limit]


class GetIncidentTool:
    """Load deterministic incident metadata without exposing its raw log."""

    name = "get_incident"
    description = (
        "Get the current incident and deterministic analysis; raw log omitted."
    )
    input_model = GetIncidentInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = GetIncidentInput.model_validate(arguments)
        with context.session_factory() as session:
            incident = session.get(IncidentRecord, validated.incident_id)
        if incident is None:
            raise ToolExecutionError("Incident not found")
        return ToolOutput(
            summary=(
                f"Loaded incident {incident.id}: {incident.classification}, "
                f"severity {incident.resolved_severity}."
            ),
            data={
                "incident_id": incident.id,
                "service": incident.service,
                "error": _bounded_text(incident.error, 1_000),
                "classification": incident.classification,
                "severity": incident.resolved_severity,
                "probable_cause": _bounded_text(incident.probable_cause, 1_500),
                "recommended_actions": incident.recommended_actions[:8],
                "created_at": incident.created_at.isoformat(),
                "raw_log_omitted": True,
            },
            evidence_references=[
                EvidenceReference(
                    evidence_type="incident", reference_id=str(incident.id)
                )
            ],
        )


class RetrieveRunbooksTool:
    """Retrieve bounded Phase 4 pgvector evidence."""

    name = "retrieve_runbooks"
    description = "Semantic search of approved local runbooks using pgvector."
    input_model = RetrieveRunbooksInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = RetrieveRunbooksInput.model_validate(arguments)
        category = {
            "authentication_error": "authentication",
            "database_connection_error": "database",
            "timeout_error": "http",
        }.get(
            context.classification or validated.classification or "",
            validated.classification,
        )
        chunks = context.retriever.retrieve_query(
            validated.query,
            top_k=min(validated.top_k, context.max_retrieval_chunks),
            category=category,
        )
        sources = [
            {
                "source_id": item.source_id,
                "chunk_id": item.chunk_id,
                "title": item.title,
                "category": item.category,
                "content": _bounded_text(item.content, 1_200),
                "similarity": round(item.similarity, 6),
            }
            for item in chunks
        ]
        return ToolOutput(
            summary=f"Retrieved {len(sources)} approved runbook chunks.",
            data={"chunks": sources},
            evidence_references=[
                EvidenceReference(
                    evidence_type="runbook",
                    reference_id=f"{item.source_id}:{item.chunk_id}",
                    source_id=item.source_id,
                    chunk_id=item.chunk_id,
                )
                for item in chunks
            ],
        )


def _incident_similarity(current: IncidentRecord, candidate: IncidentRecord) -> int:
    score = 0
    score += 4 if candidate.classification == current.classification else 0
    score += 3 if candidate.service == current.service else 0
    score += 2 if candidate.resolved_severity == current.resolved_severity else 0
    current_terms = set(re.findall(r"[a-z0-9]+", current.error.lower()))
    candidate_terms = set(re.findall(r"[a-z0-9]+", candidate.error.lower()))
    score += min(len(current_terms & candidate_terms), 3)
    return score


class SearchSimilarIncidentsTool:
    """Find deterministic metadata matches without returning raw logs."""

    name = "search_similar_incidents"
    description = "Find similar persisted incidents; raw logs are always omitted."
    input_model = IncidentScopedListInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = IncidentScopedListInput.model_validate(arguments)
        with context.session_factory() as session:
            current = session.get(IncidentRecord, validated.incident_id)
            if current is None:
                raise ToolExecutionError("Incident not found")
            candidates = list(
                session.scalars(
                    select(IncidentRecord)
                    .where(IncidentRecord.id != validated.incident_id)
                    .order_by(desc(IncidentRecord.created_at), desc(IncidentRecord.id))
                    .limit(100)
                )
            )
        ranked = sorted(
            ((_incident_similarity(current, item), item) for item in candidates),
            key=lambda row: (-row[0], -row[1].id),
        )[: validated.limit]
        matches = [
            {
                "incident_id": item.id,
                "service": item.service,
                "error": _bounded_text(item.error, 500),
                "classification": item.classification,
                "severity": item.resolved_severity,
                "probable_cause": _bounded_text(item.probable_cause, 800),
                "similarity_score": score,
                "raw_log_omitted": True,
            }
            for score, item in ranked
            if score > 0
        ]
        return ToolOutput(
            summary=f"Found {len(matches)} similar incident records.",
            data={"incidents": matches},
            evidence_references=[
                EvidenceReference(
                    evidence_type="similar_incident",
                    reference_id=str(item["incident_id"]),
                )
                for item in matches
            ],
        )


class GetPreviousResolutionsTool:
    """Load prior final resolution summaries for comparable incidents."""

    name = "get_previous_resolutions"
    description = "Get bounded prior resolution summaries, actions, and citations."
    input_model = IncidentScopedListInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = IncidentScopedListInput.model_validate(arguments)
        with context.session_factory() as session:
            current = session.get(IncidentRecord, validated.incident_id)
            if current is None:
                raise ToolExecutionError("Incident not found")
            rejected_execution = (
                select(AgentExecutionRecord.id)
                .where(
                    AgentExecutionRecord.incident_id == AIResolutionRecord.incident_id,
                    AgentExecutionRecord.status == "rejected",
                )
                .exists()
            )
            rows = session.execute(
                select(AIResolutionRecord, IncidentRecord)
                .join(
                    IncidentRecord, IncidentRecord.id == AIResolutionRecord.incident_id
                )
                .where(
                    AIResolutionRecord.incident_id != validated.incident_id,
                    AIResolutionRecord.status == "completed",
                    IncidentRecord.classification == current.classification,
                    ~rejected_execution,
                )
                .order_by(
                    desc(AIResolutionRecord.completed_at), desc(AIResolutionRecord.id)
                )
                .limit(validated.limit)
            ).all()
        resolutions = [
            {
                "incident_id": incident.id,
                "resolution_id": resolution.id,
                "service": incident.service,
                "summary": _bounded_text(resolution.summary, 800),
                "root_cause": _bounded_text(resolution.root_cause, 1_000),
                "recommended_actions": (resolution.recommended_actions or [])[:8],
                "confidence": resolution.confidence,
                "cited_sources": (resolution.cited_sources or [])[:8],
            }
            for resolution, incident in rows
        ]
        return ToolOutput(
            summary=f"Loaded {len(resolutions)} previous resolution summaries.",
            data={"resolutions": resolutions},
            evidence_references=[
                EvidenceReference(
                    evidence_type="resolution",
                    reference_id=str(item["resolution_id"]),
                )
                for item in resolutions
            ],
        )


class GetIncidentResolutionContextTool:
    """Read previously persisted Phase 4/5 retrieval context."""

    name = "get_incident_resolution_context"
    description = "Get the incident's already-persisted retrieval context, if any."
    input_model = ResolutionContextInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = ResolutionContextInput.model_validate(arguments)
        with context.session_factory() as session:
            resolution = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == validated.incident_id
                )
            )
        items = [] if resolution is None else (resolution.retrieved_context or [])[:8]
        sanitized = [
            {
                "source_id": item.get("source_id"),
                "chunk_id": item.get("chunk_id"),
                "title": _bounded_text(str(item.get("title", "")), 255),
                "category": _bounded_text(str(item.get("category", "")), 100),
                "content": _bounded_text(str(item.get("content", "")), 1_200),
                "similarity": item.get("similarity"),
            }
            for item in items
        ]
        references = [
            EvidenceReference(
                evidence_type="runbook",
                reference_id=f"{item['source_id']}:{item['chunk_id']}",
                source_id=str(item["source_id"]),
                chunk_id=int(item["chunk_id"]),
            )
            for item in sanitized
            if item.get("source_id") and isinstance(item.get("chunk_id"), int)
        ]
        return ToolOutput(
            summary=f"Loaded {len(sanitized)} persisted context chunks.",
            data={"chunks": sanitized},
            evidence_references=references,
        )


class SummarizeEvidenceTool:
    """Deterministically normalize server-held prior tool results."""

    name = "summarize_evidence"
    description = "Summarize selected prior tool evidence deterministically."
    input_model = SummarizeEvidenceInput

    def execute(self, arguments: StrictToolInput, context: ToolContext) -> ToolOutput:
        validated = SummarizeEvidenceInput.model_validate(arguments)
        unique_steps = list(dict.fromkeys(validated.step_numbers))
        results = [
            context.previous_results[number]
            for number in unique_steps
            if number in context.previous_results
        ]
        missing = [
            number for number in unique_steps if number not in context.previous_results
        ]
        references = list(
            {
                (ref.evidence_type, ref.reference_id, ref.source_id, ref.chunk_id): ref
                for result in results
                for ref in result.evidence_references
            }.values()
        )
        return ToolOutput(
            summary=(
                f"Normalized {len(results)} evidence steps; "
                f"{len(missing)} requested steps were unavailable."
            ),
            data={
                "facts": [result.summary for result in results],
                "missing_step_numbers": missing,
                "conflicts": [],
            },
            evidence_references=references,
        )


class ToolRegistry:
    """Fixed allowlist and schema catalog for server-controlled tools."""

    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        registered = tools or [
            GetIncidentTool(),
            RetrieveRunbooksTool(),
            SearchSimilarIncidentsTool(),
            GetPreviousResolutionsTool(),
            GetIncidentResolutionContextTool(),
            SummarizeEvidenceTool(),
        ]
        self._tools = {tool.name: tool for tool in registered}
        if len(self._tools) != len(registered):
            raise ValueError("Agent tool names must be unique")

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"Tool is not allowlisted: {name}") from exc

    def catalog(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


class ToolExecutor:
    """Validate authorization, size, schema, budget, and duration for calls."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float,
        max_tool_calls: int,
    ) -> None:
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self.max_tool_calls = max_tool_calls

    def execute(
        self,
        request: ToolRequest,
        context: ToolContext,
        *,
        current_tool_calls: int,
    ) -> ToolResult:
        if current_tool_calls >= self.max_tool_calls:
            raise AgentBudgetExceededError("Maximum tool-call budget reached")
        encoded_arguments = json.dumps(
            request.arguments, sort_keys=True, separators=(",", ":")
        )
        if len(encoded_arguments.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
            raise ToolExecutionError("Tool arguments exceed the size limit")
        tool = self.registry.get(request.tool_name)
        try:
            arguments = tool.input_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise ToolExecutionError(
                f"Invalid arguments for {tool.name}: {exc}"
            ) from exc
        requested_incident_id = getattr(arguments, "incident_id", context.incident_id)
        if requested_incident_id != context.incident_id:
            raise UnauthorizedToolArgumentsError(
                "Tool incident_id does not match the active execution"
            )

        started = time.perf_counter()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-tool")
        future = pool.submit(tool.execute, arguments, context)
        try:
            output = future.result(timeout=self.timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ToolTimeoutError(f"Tool timed out: {tool.name}") from exc
        except ToolExecutionError:
            raise
        except (ProviderUnavailableError, SQLAlchemyError):
            raise
        except Exception as exc:
            raise ToolExecutionError(f"Tool failed safely: {tool.name}") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        duration_ms = (time.perf_counter() - started) * 1_000
        encoded_result = output.model_dump_json()
        if len(encoded_result.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
            raise ToolExecutionError("Tool result exceeds the size limit")
        return ToolResult(
            tool_name=tool.name,
            status="succeeded",
            summary=sanitize_text(output.summary),
            data=sanitize_value(output.data),
            evidence_references=output.evidence_references,
            duration_ms=duration_ms,
        )
