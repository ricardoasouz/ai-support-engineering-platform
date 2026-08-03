# PostgreSQL Connectivity Failures

## Symptoms

Typical signals include `connection refused`, `could not translate host name`, TLS negotiation errors, password authentication failures, connection pool exhaustion, and `too many connections`. A timeout differs from an immediate refusal: a timeout often indicates routing or filtering, while refusal usually means the host is reachable but no process is listening on the target port.

## Diagnosis

Confirm the configured hostname, port, database name, and username without printing the password. Resolve the hostname from the same runtime environment as the application. Check PostgreSQL readiness, listener addresses, `pg_hba.conf`, TLS mode, certificate validity, and server connection limits. Compare active pool size plus overflow across every application replica with the server limit and reserved administrative capacity.

## Recovery

Correct DNS or connection settings, restore the database listener, or repair the allowed client network and authentication rule. If the pool is exhausted, identify leaked or long-running transactions before increasing limits. Terminate sessions only after confirming ownership and workload impact. Apply schema migrations through the normal migration tool; do not manually mutate production tables during connectivity recovery.

## Verification and prevention

Run a minimal `SELECT 1` through the application connection path, then exercise a real transaction. Track pool checkout latency, transaction duration, connection count, and authentication failures. Set bounded connection and statement timeouts, close sessions reliably, and reserve enough server connections for operations.
