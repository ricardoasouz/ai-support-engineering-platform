# Security policy

## Supported versions

Security fixes are applied to the current `0.7.x` development line. Earlier phase
snapshots are not maintained separately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository. Do not open a
public issue for an undisclosed vulnerability, and never attach credentials, `.env`
files, production incident logs, tokens, or personal data. If private reporting is
not enabled, contact the repository owner through their GitHub profile and ask for a
private reporting channel without disclosing the finding.

Include a minimal sanitized reproduction, affected version or Git SHA, impact, and
suggested remediation if known. The maintainer will acknowledge and triage reports
as availability permits; this project does not promise a commercial response SLA.

## Current security boundary

This is a local/demo platform, not an internet-ready deployment. It has strict input
bounds, read-only agent tools, structured model validation, container least-
privilege controls, dependency/secret/image scanning, and no external AI provider.
The Helm chart adds non-root pods, internal services, Secret references, and optional
NetworkPolicies, but its embedded PostgreSQL/Kafka/Ollama services remain local/demo
single-node components. It does not provide API authentication or authorization, TLS termination,
multi-tenant isolation, encrypted Kafka transport, production secret management,
high availability, backups/disaster recovery, or a formal penetration test. Do not
expose either the Compose stack or a default Helm installation directly to an
untrusted network.
