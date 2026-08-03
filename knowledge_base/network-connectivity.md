# Network Connectivity Diagnosis

## Symptoms

Network incidents include DNS lookup failures, connection timeouts, route failures, resets, TLS errors, and intermittent packet loss. An application-level HTTP error proves a connection reached a responding endpoint; it is not evidence of a basic network outage.

## Diagnosis

Test from the affected container or host, not only from an operator workstation. Resolve DNS, inspect the selected address, and test the exact destination port. Check route tables, security policies, network namespaces, proxies, service discovery, and certificate names. Compare successful and failed paths by source, destination, address family, and time. Avoid broad packet captures containing credentials unless normal diagnostics are insufficient and collection is authorized.

## Recovery

Restore the intended DNS record, route, listener, or narrowly scoped network rule. Prefer correcting service discovery over pinning a transient IP address. Roll back a recent networking change when evidence identifies it as the cause. Preserve deny-by-default controls and never disable TLS verification as a connectivity workaround.

## Verification and prevention

Verify DNS resolution and a complete application request from the original runtime environment. Check both new and reused connections. Monitor DNS error rate, connect latency, TLS failures, and resets. Use explicit readiness checks and documented dependency maps so failures can be localized quickly.
