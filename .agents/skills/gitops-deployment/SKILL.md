---
name: gitops-deployment
description: Design and verify a small declarative GitOps deployment controller that registers independently built Docker services, reconciles GHCR image digests, safely rolls out changes, coordinates a shared gateway and reports recovery state on a single VPS.
---

# GitOps Deployment Controller

Use this standard when building the repository responsible for desired application state, service registration and centralized delivery to a Docker/Compose VPS.

Keep the controller small and auditable for a single host. The GitOps model describes **what should run**; Caddy handles public HTTP(S), Docker runs containers, and each application repository still builds/tests/publishes its own images.

## Responsibility boundaries

- Server infrastructure repo owns Ubuntu/SSH/Docker Engine, foundational firewall/hardening and access policy.
- Application repos own source, CI, build/publish and an explicit immutable image/runtime contract.
- Deployment repo owns application registry, desired image versions, target host orchestration, shared proxy-network lifecycle, Caddy registration, health-gated releases and recorded deployment state.
- Do not conflate the CI image producer with the production rollout. A new GHCR image is a candidate until an approved policy selects that exact digest.
- Avoid Kubernetes, a permanent privileged daemon, a database or a broad webhook service unless a specific requirement justifies it; a constrained CI-triggered reconciler can be sufficient initially.

## Declarative service registry

- Keep a versioned, reviewable manifest per service with schema version, unique ID, image repository + immutable digest, platform, internal protocol/port, health endpoint, public domain/route when applicable, required env/secrets references, network membership and persistence policy.
- Clearly distinguish service registration (first deploy), normal version update, route change, disabling and permanent removal.
- Validate schema, IDs, host/path uniqueness, allowed registries/images, digest format, compatibility and origin/domain contract before any mutation.
- Never assume any arbitrary GitHub repository can be deployed: require a successfully published compatible artifact and explicit runtime contract.
- Treat untrusted PRs/webhook payloads as untrusted input; they must not become production commands or receive deployment credentials.
- Never store credentials, private environment files, SSH keys or tokens in manifests or container images.

## Reconciliation and rollout

- Compute a deterministic **plan** from validated desired state versus discovered actual state; report create/update/no-op, gateway-only changes and failures.
- Deploy missing services only after admission and within the operator-approved environment/release policy.
- For an unchanged digest/config, do not recreate containers. For an image-only change, target only that service; do not restart the shared gateway.
- Pull immutable digests, preserve unrelated services/volumes, ensure required networks and dependencies, apply bounded health checks, then activate or verify corresponding gateway route.
- Gate public routing to a newly created service on healthy internal behavior; keep existing healthy routes working during unrelated changes.
- Serialize conflicting reconciliations per host. Make retries idempotent where possible, but do not blindly retry mutations when remote state is unknown.
- An application removal must **not** silently delete its persistent data or revoke a public route without a defined decommission procedure.
- Track last attempted and last known-good revisions; report partial/failed convergence accurately rather than claiming success.

## Security and operations

- Use approved, narrowly scoped deploy credentials and least-privilege CI permissions; Docker socket or membership of the Docker group commonly grants host-equivalent power.
- Require explicit authorization for initial live host provisioning, destructive operations, publication of public services and production activation unless covered by a specific previously approved policy.
- Define a human-inspectable recovery path for gateway/config errors, unhealthy deployments, lost host access and VPS reboot.
- Keep tests and standard CI fully offline. Do not interpret a passing dry run as evidence of production availability.
- Keep logs redacted, bounded and structured enough to trace manifest revision, image digest, affected service and outcome.

## Implementation phases and skill routing

1. **Foundation:** define registry, schema validator, deterministic plan, mocked/stubbed host adapter, Caddy integration interface, Makefile checks and offline tests. No real secrets or VPS access.
2. **Local E2E:** use disposable test projects/containers and mock upstreams to verify registration, no-op, update, malformed input, failed health, gateway reload and preservation of unrelated services.
3. **Production integration:** separately approved host/bootstrap prerequisites, pinned image availability, protected CI configuration, verified access, DNS/TLS and staged first rollout.
4. **Operations:** bounded reconciliation policy, monitoring, restart/recovery drills, documented rollback limitations and operator runbooks.

Use [references/registry-and-reconciliation.md](./references/registry-and-reconciliation.md) when implementing manifests, validation, planner, change detection or service updates.
Use [references/safe-rollout-and-recovery.md](./references/safe-rollout-and-recovery.md) for deployment execution, staging, health gates, rollback, concurrent deployment or persistent data handling.
Use [references/auth-and-triggering.md](./references/auth-and-triggering.md) when setting up GitHub Actions, GHCR permissions, webhook/polling, remote host access or automated release policy.

## Acceptance evidence

- Verify registry validation, plan/no-op behavior and diff output without server access.
- Verify service isolation, digest pinning, failed/partial rollouts and gateway stability in local Docker E2E.
- Confirm exact CI behavior and authorization gates, not only workflow syntax.
- For live deployment, separately verify real SSH/network/DNS/TLS/health and recovery within an approved production window.
- Report the phases proven and pending. Do not call a registry YAML + Caddyfile a functioning automated deployment platform.

Follow `task-development-workflow`, `docker`, `github-actions`, `continuous-delivery` and `caddy-gateway` where relevant, without eagerly loading their optional references.
