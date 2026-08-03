# AI Support Engineering Platform

AI Support Engineering Platform is an incrementally developed incident-analysis
service. Phase 3 keeps the synchronous FastAPI and PostgreSQL behavior from the
first two phases and adds Kafka-backed asynchronous processing through a separate
worker.

The analyzer and worker remain deterministic. Phase 3 does not include an LLM,
RAG, embeddings, a vector database, Kubernetes, CI/CD, cloud deployment, or an
observability backend.

## Phase 3 architecture

```text
Client
  |
  v
FastAPI POST /api/v1/incidents
  |
  +--> Deterministic analysis
  |
  +--> PostgreSQL transaction
  |      +-- incidents
  |      +-- outbox_events (incident.created)
  |
  +--> Commit
         |
         v
   Outbox Dispatcher
         |
         +-- Publish acknowledged --> Kafka: incident.created
         |
         +-- Broker unavailable --> retain + retry
                                      |
                                      v
                               Worker Consumer Group
                                      |
                                      +-- Validate event
                                      +-- Deterministic processing
                                      +-- Insert processed_events
                                      +-- Commit Kafka offset
```

HTTP routes do not contain Kafka client code. The incident application service
owns the database transaction, the outbox dispatcher owns publication state, and
the worker owns consumption and asynchronous processing.

Important files:

```text
app/
  main.py                      # API lifecycle and outbox retry thread
  api/routes/incidents.py      # Analyze, list, and retrieve endpoints
  core/config.py               # PostgreSQL and Kafka environment settings
  db/models.py                 # Incident, outbox, and idempotency mappings
  events/models.py             # Versioned incident.created envelope
  events/producer.py           # Kafka producer and topic abstraction
  events/dispatcher.py         # Durable outbox dispatch and retry loop
  repositories/outbox.py       # Outbox persistence operations
  services/incidents.py        # Incident + outbox transaction
  workers/processor.py         # Idempotent deterministic processing
  workers/runner.py            # Manual-offset Kafka consumer loop
  workers/incident_worker.py   # Worker process entry point
migrations/versions/           # Phase 2 and Phase 3 schema revisions
docker-compose.yml             # API, PostgreSQL, Kafka, and worker
```

## Event flow and publication responsibilities

`POST /api/v1/incidents` still returns the Phase 1 analysis response and stores
the incident synchronously. In the same PostgreSQL transaction, it stages an
`outbox_events` row. Kafka publication is attempted only after the transaction
commits.

The producer abstraction:

- creates the configured topic idempotently through Kafka's admin API;
- keys records by `incident_id`, keeping one incident's order stable per
  partition;
- serializes the validated envelope as UTF-8 JSON;
- uses `acks=all`, client idempotence, bounded retries, and a delivery timeout;
- waits for broker acknowledgement before marking an outbox row published;
- reports failures to the dispatcher without rolling back the incident.

The background dispatcher retries due outbox rows with bounded exponential
backoff. Multiple API workers use row locks with `SKIP LOCKED`, so they can drain
the same outbox without intentionally publishing the same row concurrently.

## Kafka topic and event schema

Topic: `incident.created`

The Compose default is three partitions and replication factor one. Replication
factor one is appropriate only for the single-node local-development broker.
Kafka is not exposed to the host; diagnostics use the CLI inside its container.

Current envelope version:

```json
{
  "event_id": "9be20b84-4ee5-47a5-a35d-9b7597d0c355",
  "event_type": "incident.created",
  "event_version": 1,
  "occurred_at": "2026-08-03T12:30:00Z",
  "incident_id": 42,
  "service": "identity-api",
  "classification": "authentication_error",
  "severity": "high"
}
```

`event_type` and `event_version` are strict literals. Timestamps must be
timezone-aware. Unknown fields are rejected. Raw errors and logs stay in
PostgreSQL and are deliberately not duplicated into Kafka.

## Worker responsibilities

The worker subscribes with the `incident-processing-v1` consumer group and
disables automatic offset commits. For each message it:

1. validates and deserializes the versioned envelope;
2. verifies that the referenced incident is visible in PostgreSQL;
3. derives a deterministic classification/severity routing key;
4. inserts one `processed_events` record;
5. commits the Kafka offset synchronously after processing.

Malformed messages are logged and their offsets are committed so a poison
message cannot block a partition. Missing incident rows and unexpected
processing/commit errors are retryable: the worker seeks back to the same offset
and does not commit it. SIGTERM and SIGINT request graceful consumer shutdown.

## Delivery and idempotency semantics

Phase 3 provides at-least-once delivery, not end-to-end exactly-once delivery.

- The incident and outbox event are atomically durable in PostgreSQL.
- A Kafka outage may delay publication but does not delete the incident or
  pending event.
- Producer acknowledgements and idempotent producer mode reduce transport-level
  duplicates, but a crash between Kafka acknowledgement and the outbox update
  can still cause an application-level resend.
- `processed_events.event_id` is a primary key. Redeliveries and consumer
  restarts therefore produce a duplicate outcome instead of repeating work.
- Kafka offsets are committed only after successful, duplicate, or deliberately
  discarded malformed-message handling.

The `processed_events.processing_result` currently records
`prepared_for_future_ai_processing` and a deterministic routing key. It is an
extension point, not AI processing.

## PostgreSQL schema

Alembic owns all schema changes:

- `incidents`: submitted incident and deterministic analysis.
- `outbox_events`: minimal event payload, topic, attempt count, next retry,
  failure detail, and publication timestamp.
- `processed_events`: unique event ID, consumer group, referenced incident, and
  deterministic result.

Apply or inspect migrations:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current
docker compose exec api alembic check
```

## API

Interactive documentation is available at `http://127.0.0.1:8000/docs`.

Create and asynchronously dispatch an incident:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{
    "service": "identity-api",
    "error": "JWT validation failed",
    "log": "Bearer token has expired for subject 42",
    "severity": "high"
  }'
```

List or retrieve persisted incidents:

```bash
curl "http://127.0.0.1:8000/api/v1/incidents?service=identity-api&severity=high"
curl http://127.0.0.1:8000/api/v1/incidents/1
```

Exact filters support `service`, resolved `severity`, and `classification`.
Results are newest first and accept `limit` and `offset`.

## Docker Compose

Prerequisites are Docker Desktop or Docker Engine with Compose. Create local
settings and replace the example PostgreSQL password:

```powershell
Copy-Item .env.example .env
```

Start the complete stack:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f api worker kafka
```

Services:

- `db`: PostgreSQL 17 with the existing `postgres_data` volume and readiness
  check.
- `kafka`: official `apache/kafka:4.3.1`, single-node combined KRaft
  broker/controller, internal listener, CLI readiness check, and `kafka_data`
  volume.
- `api`: non-root FastAPI service. It requires PostgreSQL, runs Alembic, and can
  preserve pending events if Kafka is temporarily unavailable.
- `worker`: separate consumer process. It starts after PostgreSQL, Kafka, and the
  migrated API are healthy.

Stop containers without deleting database or Kafka volumes:

```bash
docker compose down
```

## Kafka troubleshooting

List and describe the event topic:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --list
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --describe --topic incident.created
```

Inspect events from the start without joining the worker group:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic incident.created \
  --from-beginning --max-messages 10
```

Inspect worker offsets and lag:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe \
  --group incident-processing-v1
```

Inspect durable publication and idempotency state:

```bash
docker compose exec db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT event_id, attempts, published_at, last_error FROM outbox_events;"'
docker compose exec db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT event_id, incident_id, processing_result FROM processed_events;"'
```

## Configuration

`.env.example` documents all runtime settings. Important Kafka variables are:

- `KAFKA_BOOTSTRAP_SERVERS`
- `KAFKA_INCIDENT_CREATED_TOPIC`
- `KAFKA_TOPIC_PARTITIONS`
- `KAFKA_CONSUMER_GROUP`
- `KAFKA_PRODUCER_CLIENT_ID`, `KAFKA_PRODUCER_ACKS`,
  `KAFKA_PRODUCER_ENABLE_IDEMPOTENCE`, delivery timeout, and retries
- outbox polling/batch settings
- worker client, polling, and retry settings

The local `.env` is ignored by Git. Kafka uses plaintext only inside the local
Compose network; production deployments would require authentication,
encryption, and a multi-broker topology.

## Quality checks

The normal suite does not require Kafka. It uses isolated SQLite databases,
dependency injection, and fake producer/handler adapters for deterministic tests.

```bash
python -m pytest
ruff check app tests migrations
ruff format --check app tests migrations
python -m compileall -q app tests migrations
pip-audit -r requirements.txt
docker compose config --quiet
```

## Roadmap

- **Phase 1 — complete:** FastAPI and deterministic incident analysis.
- **Phase 2 — complete:** PostgreSQL, SQLAlchemy/Alembic, retrieval APIs, Docker,
  and structured logging.
- **Phase 3 — implemented:** Kafka KRaft infrastructure, transactional outbox,
  asynchronous worker, manual offsets, and PostgreSQL idempotency.
- **Later phases:** AI-assisted processing and broader platform deployment and
  operations capabilities. These are not implemented here.
