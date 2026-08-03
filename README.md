# AI Support Engineering Platform

AI Support Engineering Platform is an incrementally developed portfolio project
for analyzing technical support incidents. Phase 2 provides a persistent FastAPI
service: deterministic incident analysis is stored in PostgreSQL and can be
retrieved through a versioned API. Docker Compose supplies a reproducible API and
database stack.

The analyzer remains deliberately rule-based. Kafka, LLMs, RAG, vector databases,
Kubernetes, cloud deployment, CI/CD, and observability backends are not part of
Phase 2.

## Phase 2 architecture

```text
Client
  |
  v
FastAPI routes + Pydantic validation (app/api, app/models)
  |
  v
Incident workflow (app/services/incidents.py)
  |                         |
  v                         v
Deterministic analyzer      Repository (app/repositories)
                            |
                            v
                  SQLAlchemy 2.x session + ORM (app/db)
                            |
                            v
                       PostgreSQL 17

Alembic migrations (migrations/) ---> PostgreSQL schema
Environment settings (app/core/) ---> API and database configuration
Structured JSON logging ------------> standard output
```

The boundaries keep HTTP handling, deterministic analysis, application workflow,
and persistence separate. A POST is analyzed first and then its input and output
are written in one database transaction. Retrieval queries go through the
repository rather than embedding SQLAlchemy operations in the routes.

Important files:

```text
app/
  main.py                    # FastAPI lifecycle and request logging
  api/routes/incidents.py    # Analyze, list, and retrieve endpoints
  core/config.py             # Environment settings
  core/logging.py            # JSON log formatter/configuration
  db/base.py                 # Declarative base and naming convention
  db/models.py               # Incident ORM mapping
  db/session.py              # Engine/session lifecycle
  models/incident.py         # Request and response schemas
  repositories/incidents.py  # Incident reads and writes
  services/analyzer.py       # Ordered deterministic rules
  services/incidents.py      # Analyze-and-persist transaction
migrations/                  # Alembic environment and revisions
tests/                       # Analyzer, API, and persistence tests
Dockerfile                   # Non-root production-style API image
docker-compose.yml           # API + PostgreSQL 17 local stack
```

## Persistence and database schema

SQLAlchemy 2.x maps the `incidents` table. Alembic owns schema changes; the API
does not call `create_all` at runtime.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer | Primary key |
| `service` | varchar(100) | Indexed, exact-match filter |
| `error` | varchar(1000) | Submitted error summary |
| `log` | text | Submitted log excerpt |
| `requested_severity` | varchar(8), nullable | Optional caller override |
| `resolved_severity` | varchar(8) | Analyzer result after override, indexed |
| `classification` | varchar(50) | Deterministic classification, indexed |
| `probable_cause` | text | Generated explanation |
| `recommended_actions` | JSON | Ordered string list |
| `created_at` | timestamp with time zone | Database-generated creation time |

Check constraints protect the supported severity and classification values. The
JSON column keeps recommendations as a structured ordered list without coupling
the schema to a fixed number of actions.

## API

Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

### Health

```bash
curl http://127.0.0.1:8000/health
```

### Analyze and persist an incident

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

The Phase 1 response contract is preserved:

```json
{
  "classification": "authentication_error",
  "severity": "high",
  "probable_cause": "The request could not be authenticated because its credentials or token were missing, invalid, expired, or failed verification.",
  "recommended_actions": [
    "Verify that the client sends a valid bearer token.",
    "Check token expiry, issuer, audience, and signing-key configuration.",
    "Review authentication service logs for rejected credentials."
  ]
}
```

`severity` is optional. `service`, `error`, and `log` are required non-empty
strings. Maximum lengths are 100, 1,000, and 20,000 characters respectively.
Unknown request fields are rejected.

### List incidents

```bash
curl "http://127.0.0.1:8000/api/v1/incidents?service=identity-api&severity=high&classification=authentication_error&limit=50&offset=0"
```

All filters are optional. `severity` filters the resolved severity. Results are
ordered newest first; `limit` is between 1 and 100 and defaults to 50. The
response contains the stored input, requested and resolved severity, full
analysis, ID, and creation timestamp.

### Retrieve one incident

```bash
curl http://127.0.0.1:8000/api/v1/incidents/1
```

An unknown ID returns `404 Not Found` with `{"detail":"Incident not found"}`.

## Docker Compose setup

Prerequisites: Docker Desktop or Docker Engine with Compose, plus the ability to
run the `postgres:17` image.

Create local settings from the example and replace `change-me` with a private
password:

```powershell
Copy-Item .env.example .env
```

On macOS or Linux:

```bash
cp .env.example .env
```

Start the stack and build the API image:

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

The stack contains:

- `db`: PostgreSQL 17, `pg_isready` health check, and the named
  `postgres_data` volume.
- `api`: non-root FastAPI container, HTTP health check, structured JSON logs,
  and health-aware dependency on `db`. It runs `alembic upgrade head` before
  starting Uvicorn.

Stop containers without deleting stored incidents:

```bash
docker compose down
```

Deleting the named volume also deletes the PostgreSQL data and is intentionally
not part of the normal shutdown command.

## Run locally with Python

Prerequisite: Python 3.11 or newer and a reachable PostgreSQL database.

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `.env` for the database reachable from the host, apply the
schema, and start the API:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

Equivalent migration commands:

```bash
alembic current
alembic history
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "describe schema change"
```

`DATABASE_URL` is required and accepts a SQLAlchemy URL. PostgreSQL uses the
`postgresql+psycopg://` driver. `LOG_LEVEL` defaults to `INFO`. Credentials are
supplied only through local environment settings and are not embedded in code or
Compose configuration.

## Quality checks

Tests use a fresh in-memory SQLite database for each test through FastAPI's
database dependency override. This keeps unit/API tests fast and deterministic
without depending on a developer's PostgreSQL container; the production mapping
and migration target PostgreSQL.

```bash
python -m pytest
ruff check app tests migrations
python -m compileall -q app tests migrations
pip-audit -r requirements.txt
```

## Roadmap

- **Phase 1 — complete:** FastAPI foundation and deterministic incident analysis.
- **Phase 2 — implemented:** PostgreSQL persistence, SQLAlchemy/Alembic,
  retrieval APIs, Docker support, structured logging, and persistence tests.
- **Later phases:** asynchronous event processing, LLM-assisted analysis and
  retrieval, platform deployment, CI/CD, and observability. These are explicitly
  outside the current implementation.
