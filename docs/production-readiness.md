# Production-readiness guide

Phases 7 and 8 establish repeatable delivery, security gates, and portable Kubernetes
packaging; they are not a production deployment. Compose remains a single-host
development topology and embedded Helm stateful services remain local/demo only.

## Environment profiles

| Profile | Intended use | Providers and infrastructure |
| --- | --- | --- |
| `development` | Local interactive work | PostgreSQL/Kafka/Ollama and optional observability through the main Compose file |
| `test` | Fast deterministic unit tests | SQLite, fake ports/adapters, no network |
| `integration` | Isolated Docker CI | PostgreSQL/pgvector, Kafka, API/worker, explicitly enabled deterministic AI providers |
| `production-like` | Configuration rehearsal | PostgreSQL required; placeholder credentials rejected by the validation command |

Fake AI providers require both `ALLOW_FAKE_PROVIDERS=true` and a `test` or
`integration` profile. They cannot be selected accidentally in development or
production-like settings. Telemetry export remains best-effort, as designed in Phase
6; core configuration and unsafe cross-field values fail fast.

## Runtime differences and required external controls

Production must replace local single-node Kafka with a replicated, authenticated,
TLS-protected cluster and appropriate topic replication/minimum ISR. PostgreSQL needs
managed backups, tested restoration, encrypted connections, credential rotation,
capacity monitoring, and migration orchestration outside competing API replicas.
Compose startup migrations are suitable locally. Helm disables per-replica migration
and knowledge ingestion and runs bounded release Jobs instead. Configure SQLAlchemy
pool sizes/timeouts against the real database budget.

An ingress or gateway must provide TLS, authentication/authorization, trusted proxy
handling, rate controls, and network policy. Secrets belong in a deployment secret
manager, not an environment file baked into an image. Ollama and telemetry endpoints
must be restricted to trusted networks. Define retention, redaction, access, backup,
and disaster-recovery policies before processing real incident data.

## Supply chain and releases

Canonical direct dependencies are pinned in `requirements*.txt`; resolved installs
are hash-locked in `requirements*.lock`. CI audits dependencies, scans Git history,
the repository, and both images, and uploads CycloneDX Python/container SBOMs. Images
carry the semantic version, Git SHA, and commit timestamp, and future publication
should retain both semantic and immutable SHA tags.

Registry publication, Sigstore/cosign signing, keyless identity, build provenance,
SLSA attestations, and artifact promotion are roadmap items. They are intentionally
not simulated without a registry or deployment trust boundary.

## Recommended GitHub branch protection

Protect `main` with pull requests, at least one approving review, stale-approval
dismissal when code changes, CODEOWNERS review where practical, resolved
conversations, and no force pushes or deletion. Require these status checks:

- `Unit and quality gates`
- `Supply-chain and image gates`
- `PostgreSQL, Kafka, API, and worker`
- `Helm, schema, and policy validation`

Require branches to be current before merge, restrict administrative bypass, and
enable private vulnerability reporting. The manual full-stack job is a release or
high-risk-change diagnostic, not a required ordinary PR check.

## Availability and data caveats

There is no HA, autoscaling, load balancer, external identity provider, service mesh,
cross-region recovery, formal SLO, or cloud deployment. The transactional outbox,
idempotent consumer, manual Kafka offsets, bounded durable agent state, and graceful
shutdown provide sound application semantics, but do not make the local dependencies
highly available.
