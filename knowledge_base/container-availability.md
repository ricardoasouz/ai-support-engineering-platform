# Container Availability and Restart Failures

## Symptoms

Availability failures include crash loops, failed health checks, out-of-memory termination, image pull errors, missing configuration, and a process that runs but is not ready. A running container is not necessarily a healthy application; readiness must exercise the dependencies required to serve traffic.

## Diagnosis

Inspect container status, exit code, restart count, health-check output, and recent application logs. Check memory and CPU limits, disk capacity, mounted files, environment variables, image architecture, and dependency readiness. Exit code 137 commonly follows forced termination or an out-of-memory kill; confirm the runtime event rather than relying on the number alone.

## Recovery

Fix the failing configuration or dependency, restore capacity, or roll back to a known-good image. Restarting can be a temporary recovery step but does not resolve a repeatable crash. Avoid infinite tight restart loops; use backoff and retain enough logs to diagnose the first failure. Apply database migrations before starting code that requires the new schema.

## Verification and prevention

Confirm the process remains healthy beyond its normal startup period and can serve a representative request. Test graceful shutdown so in-flight work and Kafka offsets are handled safely. Use immutable image tags, resource requests and limits based on measurements, health-aware dependency ordering, and alerts on restart-rate changes.
