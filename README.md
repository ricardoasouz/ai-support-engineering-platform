# AI Support Engineering Platform

Phase 5 turns the asynchronous grounded resolver into a controlled support-
engineering agent. Incident creation is still synchronous and durable. The agent
runs only in the Kafka worker, can select only six typed read-only evidence tools,
and must produce a schema-valid, citation-checked result for explicit human review.

The default stack remains local: PostgreSQL 17 with pgvector, Apache Kafka in KRaft
mode, Ollama, `qwen2.5:1.5b-instruct`, and `nomic-embed-text:v1.5`. It needs no cloud
AI API or external API key.

## Phase 5 architecture

```text
Client
  |
  v
FastAPI POST /api/v1/incidents
  |
  +-- deterministic Phase 1 analysis
  +-- one PostgreSQL transaction
        +-- incidents
        +-- outbox_events (incident.created)
  +-- return without waiting for Kafka, retrieval, or a model

Outbox dispatcher -- acknowledged publish --> Kafka incident.created
                                                  |
                                                  v
                                      incident-processing-v1 worker
                                                  |
                                      claim/resume agent_execution
                                                  |
                                  structured planner (versioned prompt)
                                                  |
                            validate tool name, schema, scope, size, budgets
                                                  |
                         +------------------------+------------------------+
                         | typed read-only evidence tools only             |
                         | PostgreSQL metadata + Phase 4 pgvector retrieval|
                         +------------------------+------------------------+
                                                  |
                               persist sanitized agent_step after each call
                                                  |
                                    structured resolver + local guardrails
                                                  |
                       persist ai_resolution + awaiting_review atomically
                                                  |
                                  insert processed_events idempotency marker
                                                  |
                                         commit Kafka offset
```

Database mappings, API routes, analyzers, provider adapters, prompts, tools,
orchestration, evaluation, and Kafka handling stay in separate modules. HTTP routes
never run inference. Kafka or Ollama failure never rolls back a persisted incident.

Important Phase 5 paths:

```text
app/agent/models.py          # strict request/state/step/final/review models
app/agent/tools.py           # fixed tool registry and validated executor
app/agent/workflow.py        # bounded durable plan/tool/final loop
app/agent/sanitization.py    # audit redaction and size bounds
app/ai/prompts/*/v1.txt      # allowlisted versioned prompt templates
app/ai/prompt_registry.py    # safe image-bundled prompt loader
app/evaluation/              # golden cases, metrics, fake runner, export CLI
app/repositories/agent.py    # execution, steps, feedback, review persistence
migrations/versions/         # SQLAlchemy/Alembic schema history
```

## Controlled agent execution

The planner returns a bounded JSON decision containing `goal`, `next_action`, an
optional `tool_name` and arguments, a short `reason_summary`, and
`expected_evidence`. The reason summary is an auditable decision description, not a
request for or record of private chain-of-thought.

The loop loads or resumes durable state, requests one structured decision, validates
the decision locally, executes at most one approved tool, persists a sanitized step,
and repeats. Before final output it requires both incident metadata and a runbook
retrieval attempt. The resolver then returns:

```json
{
  "summary": "The request used an expired JWT.",
  "root_cause": "The token lifetime elapsed.",
  "recommended_actions": ["Have an operator refresh the token and retry."],
  "confidence": 0.91,
  "cited_sources": [
    {"source_id": "runbook-jwt-authentication", "chunk_id": 12}
  ],
  "evidence_summary": "Incident metadata and the JWT runbook agree.",
  "tools_used": ["get_incident", "retrieve_runbooks"],
  "limitations": [],
  "escalation_required": false,
  "human_review_recommended": true
}
```

Local validation rejects unknown or duplicate citations and tools that did not run.
When citation-addressable evidence is unavailable, the server caps confidence at
0.5, records a limitation, and requires escalation and human review.

Execution states are `pending`, `running`, `waiting_for_tool`, `completed`,
`retryable`, `failed`, `cancelled`, `awaiting_review`, `approved`, and `rejected`.
Generated results enter `awaiting_review`; approval and rejection are explicit API
operations.

## Approved tools and guardrails

The registry contains exactly these read-only tools:

- `get_incident`: incident metadata and deterministic analysis; raw log omitted;
- `retrieve_runbooks`: bounded Phase 4 pgvector retrieval;
- `search_similar_incidents`: metadata matches without raw logs;
- `get_previous_resolutions`: bounded prior resolution summaries and citations;
- `get_incident_resolution_context`: previously persisted retrieval evidence;
- `summarize_evidence`: deterministic normalization of prior tool results.

There is no SQL, shell, filesystem, web, generic HTTP, Kubernetes, cloud, credential,
or remediation tool. Unknown names, extra or invalid fields, cross-incident IDs,
oversized arguments/results, repeated calls, timeouts, and exhausted budgets are
rejected by server code. Planner output never expands the allowlist.

The following are configurable hard limits:

- total steps and tool calls;
- repeated identical tool calls;
- retrieved chunks;
- execution and tool duration;
- structured-output repair attempts and durable model retries.

Prompt files are loaded only from a fixed name/version mapping. The database stores
prompt name and version, provider, and model—not complete prompts. Audit steps store
selected tool, sanitized arguments and result summary, evidence references, status,
duration, and timestamps. Raw logs, secrets, complete prompts, and chain-of-thought
are not stored in the agent tables.

## Provider capabilities and local inference

`LLMProvider` and `EmbeddingProvider` remain provider-neutral. LLM adapters explicitly
declare structured-output, native tool-selection, streaming, and usage-metadata
capabilities. Phase 5 uses a structured planner because Ollama's current adapter does
not claim native tool selection. Ollama usage counters are persisted when supplied;
character counts remain approximate metadata.

Incident data, retrieved runbooks, embeddings, and prompts remain inside the local
Compose network by default. This reduces third-party exposure but does not replace
host, database-volume, log, and model-volume security.

## Knowledge base and retrieval

The original repository-owned runbooks cover JWT/authentication, PostgreSQL
connectivity, HTTP timeouts, network connectivity, and container availability.
`knowledge_base/manifest.json` provides stable source IDs and versions. Deterministic
chunking and SHA-256 hashes make ingestion idempotent.

Run ingestion manually:

```bash
docker compose exec worker python -m app.knowledge.ingest
```

The default embedding width is 768. PostgreSQL stores `vector(768)` and uses an HNSW
cosine index. Changing the embedding width requires an explicit migration.

## Delivery, restart, and failure semantics

The platform provides at-least-once delivery, not exactly-once delivery.

- The incident and outbox event commit atomically before Kafka publication.
- The outbox retries broker failure without deleting the incident.
- The consumer uses manual offsets and seeks back on retryable failures.
- `agent_executions.incident_id` and `ai_resolutions.incident_id` are unique.
- Each successful tool step is durable, so a worker can resume after restart.
- Final resolution and `awaiting_review` state are durable before the processed-event
  marker and Kafka offset commit.
- `processed_events.event_id` prevents duplicate event processing.
- A crash after final persistence but before offset commit reuses the durable result
  without another model call.
- Ollama, embedding, PostgreSQL, tool-timeout, and other transient failures remain
  retryable; malformed planner/final output receives bounded repair attempts.
- Malformed Kafka envelopes are logged and acknowledged so a poison message cannot
  block its partition.

The implementation does not claim exactly-once model invocation. A crash between a
provider response and durable step commit can repeat that read-only operation, while
local persistence and idempotency prevent duplicate durable outcomes.

## PostgreSQL schema

Alembic revision `20260803_0004` adds:

- `agent_executions`: execution/event/incident IDs, lifecycle status, provider/model,
  prompt name/version, step/tool/model/retry counts, timing totals, retrieval counts,
  nullable provider usage, safe error metadata, and timestamps;
- `agent_steps`: ordered action, selected tool, sanitized arguments/result, evidence
  references, short reason summary, expected evidence, outcome, and duration;
- `resolution_feedback`: incident/resolution links, rating, review outcome, acceptance,
  edited flag, bounded comment, demo reviewer label, optional edited value, timestamp;
- additive `ai_resolutions` fields for evidence summary, tools used, limitations,
  escalation/review flags, and resolver prompt provenance.

Earlier tables remain: `incidents`, `outbox_events`, `processed_events`,
`knowledge_documents`, `knowledge_chunks`, `knowledge_embeddings`, and
`ai_resolutions`. Existing PostgreSQL, Kafka, and Ollama named volumes are preserved.

Migration commands:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current
docker compose exec api alembic check
```

## API

Interactive documentation is at `http://127.0.0.1:8000/docs`.

Create and retrieve an incident:

```bash
curl.exe -X POST http://127.0.0.1:8000/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{"service":"identity-api","error":"JWT validation failed","log":"Bearer token has expired","severity":"high"}'
curl.exe http://127.0.0.1:8000/api/v1/incidents/1
```

Read the final resolution, Phase 4 retrieval context, execution, and sanitized steps:

```bash
curl.exe http://127.0.0.1:8000/api/v1/incidents/1/resolution
curl.exe http://127.0.0.1:8000/api/v1/incidents/1/resolution/context
curl.exe http://127.0.0.1:8000/api/v1/incidents/1/agent-execution
curl.exe http://127.0.0.1:8000/api/v1/incidents/1/agent-execution/steps
```

Record feedback or make an explicit review decision:

```bash
curl.exe -X POST http://127.0.0.1:8000/api/v1/incidents/1/resolution/feedback \
  -H "Content-Type: application/json" \
  -d '{"rating":4,"comment":"Useful evidence","reviewer":"demo-reviewer"}'
curl.exe -X POST http://127.0.0.1:8000/api/v1/incidents/1/resolution/approve \
  -H "Content-Type: application/json" \
  -d '{"rating":5,"comment":"Approved","reviewer":"demo-reviewer"}'
curl.exe -X POST http://127.0.0.1:8000/api/v1/incidents/1/resolution/reject \
  -H "Content-Type: application/json" \
  -d '{"comment":"Needs revision","reviewer":"demo-reviewer"}'
```

There is intentionally no authentication in this local phase. `reviewer` is a demo
metadata label, not a verified identity. Production authorization, identity, and
audit-integrity controls remain future work. Review transitions return 409 when no
review is pending. Missing incidents return 404.

SSE streaming is deferred: durable polling endpoints are sufficient for Phase 5 and
avoid adding a second long-lived delivery path. WebSockets are not included.

## Feedback export and golden evaluation

Feedback is explicit evaluation data only. It is never used for automatic learning,
fine-tuning, prompt mutation, or autonomous behavior changes.

Export a sanitized dataset:

```bash
python -m app.evaluation.export_feedback --output evaluation_reports/feedback.json
```

The export excludes raw logs, full prompts, reason summaries, reviewer identity,
free-form comment text, and secrets. `evaluation_reports/` is ignored by Git.

Run the deterministic fake-provider golden evaluation:

```bash
python -m app.evaluation.run
python -m app.evaluation.run --output evaluation_reports/golden.json
```

The original eight cases cover expired JWT, invalid issuer/audience, PostgreSQL
connection refusal, pool exhaustion, gateway timeout, DNS/network failure, container
unavailability, and insufficient evidence. Metrics include citation validity and
precision, tool selection, budgets, schema validity, classification, completeness,
escalation behavior, latency, retrieval relevance, and fabricated-source detection.
The normal suite uses fake providers; a real Ollama evaluation remains an optional
manual check.

## Docker Compose

Copy the example and use local development-only values. `.env` is ignored by Git:

```powershell
Copy-Item .env.example .env
```

Start and inspect the stack:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f api worker ollama kafka
```

Services and jobs:

- `db`: `pgvector/pgvector:0.8.5-pg17-trixie`, health check, persistent
  `postgres_data` volume;
- `kafka`: official Apache Kafka 4.3.1, single-node KRaft, internal listener,
  persistent `kafka_data` volume;
- `api`: non-root FastAPI image, Alembic migrations on startup, host port 8000;
- `ollama`: internal-only API, health check, persistent `ollama_models` volume;
- `ollama-init`: one-shot pull of configured generation and embedding models;
- `worker`: idempotent ingestion followed by the Kafka controlled-agent consumer.

Only FastAPI is published to the host. PostgreSQL, Kafka, and Ollama remain internal.
Stop without deleting persistent volumes:

```bash
docker compose down
```

Do not add `--volumes` unless permanent local data deletion is explicitly intended.

## Troubleshooting

Inspect models, agent errors, and consumer lag:

```bash
docker compose exec ollama ollama list
docker compose logs --tail 200 worker ollama kafka
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group incident-processing-v1
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --describe --topic incident.created
```

Exercise Ollama recovery without deleting data:

```bash
docker compose stop ollama
# Create an incident and observe retryable execution state.
docker compose start ollama
docker compose logs -f worker
```

The first start may download the configured models. CPU inference is supported but
can be slow, particularly on the first request.

## Configuration

`.env.example` documents PostgreSQL, Kafka, Ollama, retrieval, and agent variables.
Phase 5 settings are:

- `AGENT_MAX_STEPS`, `AGENT_MAX_TOOL_CALLS`;
- `AGENT_MAX_REPEATED_TOOL_CALLS`, `AGENT_MAX_RETRIEVAL_CHUNKS`;
- `AGENT_MAX_DURATION_SECONDS`, `AGENT_TOOL_TIMEOUT_SECONDS`;
- `AGENT_MODEL_RETRIES`, `AGENT_REPAIR_ATTEMPTS`;
- `AGENT_PLANNER_PROMPT_VERSION`, `AGENT_RESOLVER_PROMPT_VERSION`.

Credentials are not hardcoded. Local Kafka plaintext and a single broker are
development choices, not a production security/availability design.

## Observability metadata

Structured logs and durable execution rows carry execution/event/incident IDs,
provider/model, prompt name/version, status, retries, durations, step/tool/model
counts, retrieval counts, nullable provider usage, and approximate input/output
sizes. Tool and retrieval latency are recorded separately. No Prometheus, Grafana,
OpenTelemetry, or external telemetry service is included in Phase 5.

## Quality checks

Unit tests use isolated SQLite databases, fake providers, and fake Kafka components;
the full suite does not require live PostgreSQL, Kafka, or Ollama.

```bash
python -m pytest
ruff check app tests migrations
ruff format --check app tests migrations
python -m compileall -q app tests migrations
python -m pip check
pip-audit -r requirements.txt
docker compose config --quiet
docker compose build api worker
```

## Current roadmap

- Phase 1 — complete: FastAPI and deterministic incident analysis.
- Phase 2 — complete: PostgreSQL, SQLAlchemy/Alembic, Docker, retrieval APIs,
  health checks, and structured logging.
- Phase 3 — complete: Kafka KRaft, transactional outbox, asynchronous worker,
  manual offsets, and idempotency.
- Phase 4 — complete: pgvector knowledge base, Ollama provider abstractions,
  grounded structured resolutions, citations, and durable retry state.
- Phase 5 — implemented: bounded support agent, typed evidence tools, versioned
  planner/resolver prompts, audit ledger, guardrails, human review, feedback export,
  golden evaluation, and execution metadata.
- Later phases: Kubernetes, cloud deployment, CI/CD, frontend, and broader
  observability. They are intentionally excluded here.
