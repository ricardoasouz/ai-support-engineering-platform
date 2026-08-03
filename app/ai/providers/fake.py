"""Deterministic providers restricted to test and integration profiles."""

import hashlib
import json
import math
import re

from app.ai.providers.base import ProviderCapabilities, StructuredModel

_INCIDENT_ID = re.compile(r'"incident_id"\s*:\s*(\d+)')
_CLASSIFICATION = re.compile(r'"classification"\s*:\s*"([a-z_]+)"')
_CITATION_SECTION = re.compile(r"AVAILABLE CITATIONS\s*\n(\[[^\n]*\])")


def _extract_citations(prompt: str) -> list[tuple[str, int]]:
    """Read only exact typed pairs from the server-owned citation JSON section."""
    match = _CITATION_SECTION.search(prompt)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    citations: list[tuple[str, int]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        chunk_id = item.get("chunk_id")
        if isinstance(source_id, str) and source_id and isinstance(chunk_id, int):
            citations.append((source_id, chunk_id))
    return list(dict.fromkeys(citations))[:4]


class DeterministicFakeLLMProvider:
    """Return schema-valid decisions without network or model downloads."""

    provider_name = "fake"
    model_name = "deterministic-ci-v1"
    capabilities = ProviderCapabilities(
        structured_output=True,
        native_tool_selection=False,
        streaming=False,
        token_usage=False,
    )
    last_usage: None = None

    def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> StructuredModel:
        name = response_model.__name__
        incident_match = _INCIDENT_ID.search(prompt)
        incident_id = int(incident_match.group(1)) if incident_match else 1
        classification_match = _CLASSIFICATION.search(prompt)
        classification = (
            classification_match.group(1) if classification_match else "unknown_error"
        )
        if name == "RequiredIncidentDecision":
            payload: dict[str, object] = {
                "goal": "Load sanitized incident metadata.",
                "next_action": "tool",
                "tool_name": "get_incident",
                "tool_arguments": {"incident_id": incident_id},
                "reason_summary": "Incident evidence is required.",
                "expected_evidence": "Deterministic incident metadata.",
            }
        elif name == "RequiredRetrievalDecision":
            payload = {
                "goal": "Retrieve an approved runbook.",
                "next_action": "tool",
                "tool_name": "retrieve_runbooks",
                "tool_arguments": {
                    "query": f"{classification} diagnostic runbook",
                    "classification": classification,
                    "top_k": 4,
                },
                "reason_summary": "Grounded runbook evidence is required.",
                "expected_evidence": "Citation-addressable runbook chunks.",
            }
        elif name == "PlannerDecision":
            payload = {
                "goal": "Produce a grounded and reviewable resolution.",
                "next_action": "final",
                "tool_name": None,
                "tool_arguments": None,
                "reason_summary": "Mandatory evidence has been gathered.",
                "expected_evidence": "A cited final response.",
            }
        elif name == "AgentFinalResponse":
            citations = _extract_citations(prompt)
            payload = {
                "summary": "The deterministic integration provider completed analysis.",
                "root_cause": (
                    "The persisted incident and approved runbook evidence identify "
                    f"a {classification} condition."
                ),
                "recommended_actions": [
                    "Have an operator follow the cited runbook diagnostics."
                ],
                "confidence": 0.8 if citations else 0.4,
                "cited_sources": [
                    {"source_id": source_id, "chunk_id": chunk_id}
                    for source_id, chunk_id in citations
                ],
                "evidence_summary": (
                    "Sanitized incident metadata and local runbook retrieval were used."
                ),
                "tools_used": ["get_incident", "retrieve_runbooks"],
                "limitations": []
                if citations
                else ["No runbook citation was returned."],
                "escalation_required": not bool(citations),
                "human_review_recommended": True,
            }
        else:
            raise ValueError(f"Fake provider does not support schema: {name}")
        return response_model.model_validate(payload)

    def close(self) -> None:
        """No-op for protocol parity."""


class DeterministicFakeEmbeddingProvider:
    """Create stable fixed-width vectors from SHAKE-256 output."""

    provider_name = "fake"
    model_name = "deterministic-shake256-v1"

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.shake_256(text.encode("utf-8")).digest(self._dimensions)
            values = [(byte - 127.5) / 127.5 for byte in digest]
            magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / magnitude for value in values])
        return vectors

    def close(self) -> None:
        """No-op for protocol parity."""
