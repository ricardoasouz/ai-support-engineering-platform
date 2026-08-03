# HTTP Timeout Diagnosis

## Symptoms

Timeout incidents may be connect timeouts, TLS handshake timeouts, response-header timeouts, read timeouts, or an upstream gateway deadline. Logs should identify which phase expired and the configured budget. Repeated retries can amplify load and turn a localized slowdown into a broad outage.

## Diagnosis

Trace the request through each hop using a request or correlation identifier. Compare client timeout, proxy timeout, server processing time, and downstream dependency latency. Check whether failures correlate with a route, payload size, dependency, availability zone, or recent deployment. Inspect saturation signals such as worker concurrency, queues, database pool waits, and CPU rather than assuming the network is at fault.

## Recovery

Remove the bottleneck or restore the slow dependency. Use retries only for safe, idempotent operations, with exponential backoff, jitter, and a strict retry budget. Do not simply raise every timeout: preserve an end-to-end deadline and leave time for callers to handle failure. Shed nonessential load when queues or concurrency limits are saturated.

## Verification and prevention

Verify latency at representative percentiles and confirm that retry volume is stable. Define per-hop budgets below the caller deadline, enforce bounded concurrency, and use circuit breaking where a failing dependency would otherwise consume all workers. Load-test timeout and cancellation behavior as well as successful requests.
