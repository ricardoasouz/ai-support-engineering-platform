"""Deterministic metrics for controlled-agent golden evaluation."""

import json
from collections.abc import Callable
from pathlib import Path

from app.evaluation.models import (
    CaseMetrics,
    EvaluationCandidate,
    EvaluationReport,
    GoldenCase,
)


def load_golden_cases(path: Path | None = None) -> list[GoldenCase]:
    """Load the original, version-controlled Phase 5 case set."""
    source = path or Path(__file__).with_name("golden_cases.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    return [GoldenCase.model_validate(item) for item in payload["cases"]]


def score_case(case: GoldenCase, candidate: EvaluationCandidate) -> CaseMetrics:
    """Score evidence validity, control compliance, completeness, and relevance."""
    expected_sources = set(case.expected_source_ids)
    retrieved_sources = set(candidate.retrieved_source_ids)
    cited_sources = {item.source_id for item in candidate.final.cited_sources}
    valid_citations = cited_sources & retrieved_sources
    citation_validity = (
        len(valid_citations) / len(cited_sources) if cited_sources else 1.0
    )
    citation_precision = (
        len(cited_sources & expected_sources) / len(cited_sources)
        if cited_sources
        else (1.0 if not expected_sources else 0.0)
    )
    required_tools = set(case.required_tools)
    used_tools = set(candidate.tools_used)
    tool_selection = (
        len(required_tools & used_tools) / len(required_tools)
        if required_tools
        else 1.0
    )
    retrieval_relevance = (
        len(expected_sources & retrieved_sources) / len(expected_sources)
        if expected_sources
        else 1.0
    )
    return CaseMetrics(
        case_id=case.case_id,
        citation_validity=citation_validity,
        citation_precision=citation_precision,
        tool_selection=tool_selection,
        within_budgets=(
            candidate.steps <= case.max_steps
            and candidate.tool_calls <= case.max_tool_calls
        ),
        schema_valid=True,
        classification_correct=(
            candidate.classification == case.expected_classification
        ),
        response_complete=bool(
            candidate.final.summary
            and candidate.final.root_cause
            and candidate.final.recommended_actions
            and candidate.final.evidence_summary
        ),
        escalation_correct=(
            candidate.final.escalation_required == case.expect_escalation
        ),
        retrieval_relevance=retrieval_relevance,
        no_fabricated_sources=cited_sources <= retrieved_sources,
        latency_ms=candidate.latency_ms,
    )


def evaluate(
    candidate_factory: Callable[[GoldenCase], EvaluationCandidate],
    *,
    provider: str,
    model: str,
    cases: list[GoldenCase] | None = None,
) -> EvaluationReport:
    """Run a provider-independent candidate factory and aggregate stable metrics."""
    selected_cases = cases or load_golden_cases()
    metrics = [score_case(case, candidate_factory(case)) for case in selected_cases]
    boolean_fields = (
        "within_budgets",
        "schema_valid",
        "classification_correct",
        "response_complete",
        "escalation_correct",
        "no_fabricated_sources",
    )
    numeric_fields = (
        "citation_validity",
        "citation_precision",
        "tool_selection",
        "retrieval_relevance",
    )
    count = max(len(metrics), 1)
    aggregate = {
        field: sum(float(getattr(item, field)) for item in metrics) / count
        for field in (*numeric_fields, *boolean_fields)
    }
    aggregate["mean_latency_ms"] = sum(item.latency_ms for item in metrics) / count
    return EvaluationReport(
        dataset_version="phase5-v1",
        provider=provider,
        model=model,
        cases=metrics,
        aggregate=aggregate,
    )
