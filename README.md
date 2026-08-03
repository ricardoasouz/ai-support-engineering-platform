# AI Support Engineering Platform

AI Support Engineering Platform is an incrementally developed portfolio project
for analyzing technical support incidents. Phase 1 provides the API foundation
for the planned cloud-native platform: a validated FastAPI service that
categorizes common failure signals and returns practical troubleshooting
guidance.

The analyzer is deliberately rule-based in this phase. It does not use an LLM,
persist data, or include deployment infrastructure yet.

## Current architecture

```text
Client
  |
  v
FastAPI routes (app/api)
  |
  +--> Pydantic validation (app/models)
  |
  +--> Deterministic analyzer (app/services)
```

```text
app/
  main.py                 # FastAPI application
  api/
    router.py             # Route composition
    routes/
      health.py           # Health endpoint
      incidents.py        # Incident analysis endpoint
  models/
    health.py             # Health response schema
    incident.py           # Incident request/response schemas
  services/
    analyzer.py           # Ordered classification rules
tests/
  conftest.py             # Shared API test client
  test_api.py             # HTTP contract and validation tests
  test_analyzer.py        # Analyzer unit and precedence tests
requirements.txt          # Runtime dependencies
requirements-dev.txt      # Runtime plus test dependencies
```

## Current functionality

- Health reporting through `GET /health`
- Incident analysis through `POST /api/v1/incidents`
- Pydantic validation for all request and response payloads
- Deterministic recognition of:
  - authentication and JWT errors
  - database connection errors
  - timeout errors
  - unknown errors
- Default severity assignment with optional caller-provided severity override
- Probable cause and recommended troubleshooting actions
- Interactive OpenAPI documentation at `/docs`

Rules are evaluated in a fixed order: authentication, database connection, then
timeout. This makes results reproducible when an incident contains overlapping
signals. Unknown incidents and timeout incidents default to `medium`,
authentication incidents to `high`, and database connection incidents to
`critical`. Accepted severity values are `low`, `medium`, `high`, and `critical`.

The incident endpoint returns `200 OK` because Phase 1 computes and returns an
analysis without creating or persisting a resource. Invalid request bodies use
FastAPI's standard `422 Unprocessable Content` validation response.

## API examples

Check service health:

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "healthy",
  "service": "ai-support-engineering-platform"
}
```

Analyze an incident:

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

Example response:

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
strings. The maximum accepted lengths are 100 characters for `service`, 1,000
for `error`, and 20,000 for `log`. Undeclared request fields are rejected.

## Run locally

Prerequisites: Python 3.11 or newer.

Create and activate a virtual environment on macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the runtime dependencies and start the development server:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

For development, install the test dependencies and run the complete suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

No environment variables are required in Phase 1. `.env.example` is included as
the placeholder for configuration introduced by future phases.

## Planned roadmap

- **Phase 2:** PostgreSQL persistence and Docker-based local development
- **Later phases:** LLM-assisted analysis and retrieval-augmented generation,
  Kubernetes, CI/CD, cloud deployment, and observability

These roadmap items are planned and are not implemented in the current version.
