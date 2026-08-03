# Kubernetes and Helm deployment

Phase 8 packages the existing application as a portable Helm chart. Kubernetes is
a deployment target, not an application rewrite. Docker Compose remains the normal
single-host development path.

## Architecture

```text
                    Ingress (optional)
                           |
                           v
                     API Service
                           |
                    API Deployment
                     /     |      \
                    /      |       \
             PostgreSQL   Kafka    Ollama
                 |          |         |
                 |          v         |
                 |      Worker        |
                 |     Deployment     |
                 |          |         |
                 +----------+---------+
                            |
                        Agent / RAG

API + Worker -> OTel Collector -> Tempo
                         |
                         +---------> Prometheus
                                      |      |
                                      +-- Grafana <-- Tempo
```

API and worker are independent Deployments. PostgreSQL and Kafka are single-replica
StatefulSets only when their local/demo switches are enabled. Ollama and the
observability components are optional. All services are ClusterIP by default;
PostgreSQL, Kafka, Ollama, Tempo, Prometheus, and the Collector have no public
service type.

The chart is under `deploy/helm/ai-support-platform`. Chart version `0.1.0` tracks
packaging changes; `appVersion` tracks the root `VERSION` (`0.7.0`). They are
deliberately independent. A future image registry should use the immutable Git SHA
as `image.tag`; no image is published by this phase.

## Prerequisites and local workflow

The supported local implementation is Kind 0.31.0 with the digest-pinned Kubernetes
1.35 node image. Docker Desktop must be running, with enough memory for PostgreSQL,
Kafka, and optionally Ollama. Install Helm 4.2.0, Kind 0.31.0, and the Docker Desktop
`kubectl` on `PATH`.

PowerShell helpers work entirely in an isolated `ai-support-phase8` cluster:

```powershell
./scripts/k8s/install-tools.ps1
./scripts/k8s/create-cluster.ps1
./scripts/k8s/build-load-images.ps1 -ImageTag dev
./scripts/k8s/install.ps1 -ImageTag dev
./scripts/k8s/smoke-test.ps1
./scripts/k8s/destroy.ps1
```

The install helper generates a random local-only password, creates the namespace
and Secret, and passes no secret through Helm values. To run the smaller deterministic
stack used by hosted CI:

```powershell
./scripts/k8s/install.ps1 -ImageTag dev -UseFakeProviders -DisableObservability
```

Docker Desktop Kubernetes can render/install the same chart, but Kind is the
documented reproducible path and has a dedicated cluster lifecycle. Local images
are built from the Phase 7 Distroless Dockerfile and loaded with
`kind load docker-image`; Docker Hub publication is not assumed.

## Secrets and configuration

`values.yaml` contains non-sensitive settings only. By default the chart references
`ai-support-platform-secrets`, whose keys are `database-url`, `postgres-password`,
and `grafana-admin-password`.

For production-like usage, create that Secret through the deployment environment
or set `secrets.existingSecret` to another name. `secrets.create` exists for explicit
controlled automation, but putting its values in a committed values file is
forbidden. External Secrets Operator, Vault, and cloud secret managers are compatible
future integrations; none is installed by this chart.

The shared ConfigMap maps directly to Phase 7 environment settings. API and worker
override only `OTEL_SERVICE_NAME`. Configuration validation remains inside the
application, fake providers remain restricted to `test`/`integration`, and
telemetry export remains best-effort.

## Migrations and knowledge ingestion

Kubernetes sets both per-pod lifecycle toggles to false. Every release revision
creates one normal, revision-scoped Job that runs `alembic upgrade head`. API and
worker init containers retry `alembic check`, so application processes cannot start
against an unmigrated schema. A later ordered Helm hook ingests the bundled knowledge
base after the optional missing-model pull. Jobs use bounded retries and the same
immutable image and Secret as the workloads. This avoids migration races, immutable
Job upgrade conflicts, and duplicate ingestion when replicas scale.

Hooks never run a schema downgrade. A Helm rollback restores workload/configuration
manifests but does not reverse database migrations. Backward-compatible migrations
or a deliberate forward-fix are required for real releases.

## Storage and local stateful services

Default PVCs use `ReadWriteOnce` and the cluster's default StorageClass:

- PostgreSQL data: 8 GiB
- Kafka log data: 8 GiB
- Ollama models: 12 GiB
- Prometheus and Tempo: 4 GiB each
- Grafana: 2 GiB

StorageClass and size are configurable per component. Ollama's model-init Job asks
the running Ollama server to pull only missing models, so downloads land in the
persistent model PVC rather than repeating on each pod restart. CPU-only inference
is the default and is slow; `values-gpu.yaml` shows an optional NVIDIA resource
request without installing or assuming a device plugin.

Embedded PostgreSQL/pgvector and single-node Kafka KRaft are for local/demo use.
They are not HA, have one PVC and one failure domain, and do not replace managed or
operator-backed production architectures.

## External-service mode

`values-external.yaml` disables PostgreSQL, Kafka, Ollama, and the local
observability stack. The database URL remains in the existing Secret; Kafka and
provider endpoints map to non-sensitive values. Replace the example internal DNS
names before installation.

When NetworkPolicies are enabled with external services, populate
`networkPolicy.externalEgressCidrs` with the smallest correct destination CIDRs.
The provided external example disables policies until those deployment-specific
boundaries are known rather than pretending that DNS names can be used in standard
NetworkPolicy rules.

## Security and networking

API, worker, and Jobs use UID/GID 10001, `runAsNonRoot`, RuntimeDefault seccomp,
read-only root filesystems, no privilege escalation, and no Linux capabilities.
Dedicated ServiceAccounts have token automount disabled and no Roles or bindings.
There are no host paths, Docker socket mounts, host namespaces, or privileged pods.
The install helper labels its isolated namespace to enforce, audit, and warn against
the Kubernetes `restricted` Pod Security Standard.

PostgreSQL, Kafka, Ollama, and observability images use their documented non-root
UIDs. Kafka keeps a writable root filesystem because the official image generates
runtime configuration; data remains on its PVC and privilege escalation/capabilities
stay disabled. This exception is not inherited by application containers.

NetworkPolicies establish default deny, DNS access, and the minimum application,
stateful, and observability paths. Ollama alone may use HTTP/HTTPS egress while model
downloads are enabled. Enforcement depends on the cluster CNI; Kind's default
networking may not enforce these policies, so their presence is not proof of runtime
enforcement.

## Probes, resources, and scaling

API uses startup, readiness, and liveness HTTP probes against `/health`. Worker uses
the existing `/tmp/incident-worker-ready` marker and requires no artificial HTTP
server. PostgreSQL uses `pg_isready`; Kafka uses its topic CLI and TCP liveness;
Ollama uses `/api/tags`, never model inference.

Every container has requests and limits. Defaults are development starting points,
not capacity recommendations. Profile real latency, model memory, pool budgets, and
Kafka throughput before production sizing.

API and worker can scale independently:

```bash
kubectl -n ai-support-phase8 scale deployment/ai-support-api --replicas=2
kubectl -n ai-support-phase8 scale deployment/ai-support-worker --replicas=2
```

Workers retain one consumer group, so Kafka partitions distribute between replicas.
Useful concurrency cannot exceed the `incident.created` partition count. Idempotent
event/execution tables and manual offsets preserve at-least-once behavior across
restarts. Worker autoscaling is intentionally manual; production should scale on
Kafka lag through future custom metrics/KEDA rather than CPU alone.

API HPA is optional and disabled. It uses CPU and optional memory utilization and
requires Metrics Server. PDBs render only when a stateless component has more than
one configured replica. Anti-affinity/topology-spread settings are optional so a
single-node Kind cluster remains schedulable.

## Observability

The optional Collector receives API/outbox/Kafka/worker/agent/RAG/Ollama OTLP spans
and metrics. Prometheus scrapes the Collector's exporter; Tempo stores traces;
Grafana provisions both data sources. This preserves Kafka trace headers and event
semantics. Use port-forwarding rather than public services:

```bash
kubectl -n ai-support-phase8 port-forward service/ai-support-grafana 3000:3000
```

If the Collector is unavailable, exporter failures must not prevent persistence or
worker processing. The chart does not add Prometheus Operator or ServiceMonitors.

## Rollouts, rollback, and recovery tests

Use an immutable tag for upgrades:

```bash
helm upgrade ai-support deploy/helm/ai-support-platform \
  -n ai-support-phase8 --reuse-values --set-string image.tag=<git-sha> \
  --wait --wait-for-jobs
helm history ai-support -n ai-support-phase8
helm rollback ai-support 1 -n ai-support-phase8 --wait --wait-for-jobs
```

Deployments use rolling updates with zero unavailable pods; single-writer PVC
workloads use `Recreate`. Deleting API or worker pods should cause Deployment
replacement. Deleting PostgreSQL, Kafka, or Ollama pods should retain their PVC data,
but the single-node services are unavailable while restarting. Collector loss should
degrade telemetry only. The manual Kind workflow exercises API/worker recreation,
PostgreSQL/Kafka persistence, two-replica scaling, Helm upgrade, and rollback.

For a dedicated disposable cluster, the remaining manual resilience probes are:

```bash
kubectl -n ai-support-phase8 delete pod -l app.kubernetes.io/component=ollama
kubectl -n ai-support-phase8 rollout status deployment/ai-support-ollama
kubectl -n ai-support-phase8 scale deployment/ai-support-otel-collector --replicas=0
# Run smoke-test.ps1; persistence and agent processing should still complete.
kubectl -n ai-support-phase8 scale deployment/ai-support-otel-collector --replicas=1
```

After Ollama restarts, `ollama show` through the service should find models already
stored on the PVC. During either single-node stateful restart, temporary retry or
unavailability is expected; the chart does not claim zero downtime.

## Validation and troubleshooting

```bash
python scripts/k8s/validate-chart.py
helm lint deploy/helm/ai-support-platform
helm template ai-support deploy/helm/ai-support-platform > rendered.yaml
kubeconform -strict -summary -kubernetes-version 1.35.0 rendered.yaml
python scripts/k8s/validate-rendered-manifests.py rendered.yaml
trivy config --severity HIGH,CRITICAL --exit-code 1 deploy/helm/ai-support-platform rendered.yaml
```

Useful diagnostics:

```bash
kubectl get pods,pvc,jobs -n ai-support-phase8
kubectl get events -n ai-support-phase8 --sort-by=.lastTimestamp
kubectl logs -n ai-support-phase8 deployment/ai-support-api
kubectl logs -n ai-support-phase8 deployment/ai-support-worker
kubectl describe pod -n ai-support-phase8 <pod>
helm get manifest ai-support -n ai-support-phase8
```

Common local failures are insufficient Docker memory, an image not loaded into Kind,
a missing Secret key, no default StorageClass, slow CPU Ollama pulls, or a CNI that
does not enforce NetworkPolicy. Neither Helm nor Kubernetes makes the embedded
stateful services highly available.
