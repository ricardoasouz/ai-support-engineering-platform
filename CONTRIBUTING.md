# Contributing

## Setup

Python 3.14, Docker Desktop or Docker Engine with Compose v2, and Git are required.
On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
Copy-Item .env.example .env
```

On Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
cp .env.example .env
```

Replace all `change-me` values in the ignored `.env`; never commit that file. Check
configuration with `python scripts/validate_config.py --env-file .env`.

## Development and validation

Start the local Phase 1-8 Compose stack with `docker compose up -d --build --wait`. Apply or
inspect migrations with `docker compose exec api alembic upgrade head`, `alembic
current`, and `alembic check`.

Before opening a pull request, run:

```bash
python -m pytest -m unit
ruff check app tests integration_tests scripts migrations
ruff format --check app tests integration_tests scripts migrations
python -m compileall -q app tests integration_tests scripts migrations
python -m pip check
pip-audit --require-hashes -r requirements.lock
docker compose config --quiet
python scripts/k8s/validate-chart.py
helm lint deploy/helm/ai-support-platform
git diff --check
```

Docker-backed tests use `docker-compose.integration.yml`, an explicit integration
project name, and their own ephemeral credentials/volumes. See the README for the
exact commands. Never run `down --volumes` against the normal developer project if
its persistent data is needed.

Kubernetes changes must render the default, CI, development, and external-service
profiles; pass kubeconform and the rendered-manifest policy script; and remain
installable in the isolated Kind cluster. Use only `scripts/k8s/destroy.ps1` for the
dedicated `ai-support-phase8` cluster. Never point a test chart at Compose storage.

## Dependencies, migrations, and versioning

Edit the small canonical `requirements.txt` or `requirements-dev.txt`, then regenerate
both applicable hash locks with `uv pip compile --universal --python-version 3.14
--generate-hashes`. Review transitive changes before submission. Schema changes must
include an Alembic revision and migration tests. User-visible releases update the
single root `VERSION`; release tags are exactly `v<VERSION>`. Helm chart versions
track packaging changes independently, while `Chart.appVersion` must match `VERSION`.

Use focused, imperative commit subjects such as `fix: preserve outbox retry state`.
Keep commits reviewable, document behavior/config changes, include regression tests,
and report whether integration and security checks ran. Do not commit generated
SBOMs, scan reports, model files, credentials, private logs, or hidden reasoning.
