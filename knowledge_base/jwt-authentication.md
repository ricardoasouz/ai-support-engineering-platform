# JWT and Authentication Failures

## Symptoms

Authentication failures commonly appear as HTTP 401 responses, `JWT expired`, `invalid signature`, `unknown key id`, or `token not active` messages. Distinguish authentication failures from HTTP 403 authorization failures: a 401 usually means identity could not be established, while a 403 usually means an authenticated identity lacks permission.

## Diagnosis

Inspect the token issuer, audience, expiry (`exp`), not-before (`nbf`), algorithm, and key identifier (`kid`) without logging the complete token. Compare application time with a trusted clock. Confirm that the service is using the expected issuer metadata and that its signing-key cache contains the active key. A sudden failure after key rotation often indicates stale JWKS data; failures isolated to old sessions usually indicate expired tokens.

## Recovery

For expired credentials, refresh the token or require re-authentication. For clock skew, restore time synchronization before widening validation tolerances. For a stale signing key, refresh the JWKS cache from the trusted issuer and verify the key identifier before retrying. Never bypass signature, issuer, or audience validation to restore service.

## Verification and prevention

Verify recovery with a newly issued token and a negative test using an invalid audience. Monitor authentication failure rates by reason while redacting credentials. Keep signing-key rotation procedures documented, use short cache refresh intervals with bounded fallback, and alert on system clock drift.
