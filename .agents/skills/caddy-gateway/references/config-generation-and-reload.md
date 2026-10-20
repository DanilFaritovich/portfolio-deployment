# Caddy Configuration Generation and Safe Reload

Read this reference when implementing a declarative registry -> Caddy route generator or gateway configuration update process.

## Input model

- Define one authoritative schema version. Include normalized hostname, service ID, internal upstream alias/port and any explicit path rules.
- Check required fields, hostname syntax, ID uniqueness, case-normalized duplicate hostnames, conflicting paths and reserved/internal-only domains before rendering.
- Prevent Caddyfile/config injection. Prefer structured JSON configuration or tightly constrained templates; never paste arbitrary untrusted strings into Caddy directives.
- Require an explicit default/catch-all strategy. A service added to the registry must not inadvertently shadow another project's route.
- Keep manual gateway-wide settings isolated from generated per-service entries, with deterministic ordering and stable formatting.

## Safe application sequence

1. Read the desired registry revision and compare route-relevant fields with the active accepted revision.
2. If nothing route-relevant changed, do not reload Caddy.
3. Generate complete candidate config to a temporary, permission-safe location; compare with the previously accepted configuration.
4. Run a pinned compatible Caddy adapter/validation against the **complete candidate** (for example `caddy validate --config ... --adapter caddyfile` for Caddyfile input).
5. Preserve current known-good configuration and metadata. Do not replace it before candidate validation.
6. Activate with the supported reload command/API against the correct running Caddy instance, with a serialization lock against concurrent writers.
7. Verify the active configuration and the affected route(s) using bounded health/smoke checks. A successful syntax check alone is not evidence of an effective reload.
8. On failure, retain/restore the previous known-good config and attempt a safe restore only when the active state is known; otherwise stop and raise a recovery-needed status rather than repeating blind mutations.

## Testing and release evidence

- Use a containerized Caddy matching the production version and lightweight, distinct mock upstreams.
- Verify two hosts route to different upstreams; adding/changing one route preserves the other.
- Verify invalid hostnames, duplicates, missing upstream aliases/ports, injection attempts and a malformed generated candidate fail before a reload.
- Verify a working active config remains functional after invalid candidate rejection.
- Exercise a valid config reload without recreating the gateway container, as supported by the chosen runtime.
- Do not require public DNS or a Let's Encrypt certificate to validate local routing; mark real TLS issuance as production-only.
- Avoid claiming atomic multi-service changes when only per-component reload/rollback is implemented. Gateway config activation and application rollout are separate recovery domains.
