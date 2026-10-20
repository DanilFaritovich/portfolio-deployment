# Safe Single-VPS Rollout and Recovery

Use this reference when applying changes to Docker services, attaching networks, exposing routes, monitoring readiness or planning rollback.

## Safe sequence

1. Resolve exact desired and currently active image digests, config versions and last known-good state before mutation.
2. Lock the affected host deployment. Ensure baseline gateway and other apps are not accidentally targeted.
3. Pull/verify the new image by immutable digest and validate rendered Compose/config before replacing a running service.
4. Prepare networks, named volumes and secrets as separate controlled prerequisites; never delete state to make a rollout pass.
5. Apply only the specific service. For stateful services, define backup/restore and migration compatibility separately.
6. Wait for startup/readiness with a bounded deadline and collect only relevant logs on failure.
7. For a new public service, attach its validated gateway route **after** internal health verification; then check the gateway route. For an image-only update, preserve existing route and shared gateway process.
8. Record accepted actual digest, health result and activated route. Publish a compact, truthful outcome.

## Failure handling

- If image pull/admission fails before mutation, keep the current deployment unchanged.
- If a new container is unhealthy, attempt rollback to the known-good image/config **only when** its identity and restoration procedure are confirmed.
- If the controller crashes mid-deploy, the next run must inspect the actual host state before planning any retry.
- If a Caddy candidate is invalid, reject the change and retain the last accepted gateway routes.
- If Caddy reload or post-reload smoke fails, treat gateway and application state separately; preserve unaffected routes and escalate when an automatic correction is unsafe.
- Image rollback is not database rollback. Non-reversible migrations and persistent data changes require separate recovery plans and possibly human intervention.
- De-registering a stateful app is not permission to prune Docker volumes, images, certificates, storage directories or secrets.

## Resilience and real-world tests

- Restart policies recover exited processes; Docker's unhealthy status alone does not restart them.
- VPS reboot recovery requires Docker service startup, Compose/service restart policy, persistent gateway certificate storage, and verification of actual recovery.
- Total host loss or loss of DNS/certificate accessibility is not repaired by a controller running only on the lost host; keep console recovery, backups and off-host monitoring.
- Use local mock failure scenarios: failed pull, image exit, unhealthy healthcheck, malformed gateway config, controller interruption and repeated identical apply.
- Distinguish best-effort sequential rollout from genuinely zero-downtime or transactional deployment; do not claim atomicity without a demonstrated mechanism.
- Record which recovery paths were exercised locally, which need a controlled VPS drill and which remain manual.

## Production boundary

No live VPS change, TLS/DNS modification, public service addition or destructive recovery action without explicit authorization or a documented operator-approved automatic deployment policy. CI's passing test result only qualifies a candidate, not permission to deploy it.
