# Image Producer for a Separate Deployment Controller

Read this reference when an application repository builds/publishes a container image but another repository or controller owns production deployment, gateway configuration, and reconciliation.

## Responsibility boundary

The application repository owns source validation, deterministic Docker build, container smoke tests, image publication and a documented runtime contract.

The deployment controller owns desired application versions, network/service registration, secrets, server rollouts, health gates, gateway routing and recovery.

Never put host SSH, Caddy configuration, other-service orchestration or unconditional production updates into the application repository merely because it publishes images.

A published image is an available candidate; it is not evidence that the image has been deployed.

## Trusted publication

- PR jobs validate and build but do not publish using privileged package credentials.
- Publish trusted branch/commit and authorized release-tag artifacts only after the exact source has satisfied required checks. Do not assume an independently triggered publishing workflow is correctly gated just because branch protection exists.
- Keep `contents: read` and `packages: write` scoped to the jobs that require them.
- Use a fixed image name, commit-SHA tag for traceability, an immutable digest for deployment, and optional version tags for explicitly authorized releases.
- Avoid production reliance on mutable `latest`, `main` or `develop` tags. Preserve OCI source/revision metadata where useful.
- Use Buildx/cache when beneficial; cache hits do not substitute for tests or trusted provenance.
- Report the actual registry digest only when publication happened and it was retrieved; otherwise mark publication pending.

## Runtime contract for the controller

Publish a safe sample or documented contract containing:

- stable application ID and GHCR image name;
- required immutable image reference/digest input;
- internal listen port and healthcheck path;
- HTTP route expectations and internal gateway network/service alias;
- stateless versus persistent storage requirements;
- build-time public configuration (such as SSG origin) versus runtime secrets;
- architecture/platform expectations and any startup dependencies;
- deployment/rollback limitations and owner-approved release policy.

Do not hardcode real production domain or server host if not yet chosen. Do not commit credentials or register services with a live controller during foundation work.

## Workflow split and handoff

- Separate source CI, registry publishing and server deployment milestones in reporting.
- Before the controller exists, test image build, runtime HTTP, health, Compose and workflow validation locally/CI.
- For an image update event, the controller must verify provenance and the exact digest and coordinate a separately authorized rollout; mutable tag movement alone does not imply a safe deploy.
- Prefer declarative service registration: adding a registry entry should validate its image/port/network/domain contracts before deploying a missing app or updating its gateway route.
- Only update the affected service when image identity changes. Only reload gateway configuration when routing configuration changes; avoid restarting the shared gateway for an ordinary frontend deploy.
- Do not automatically delete persistent data when an application manifest is removed.
- Keep single-host Docker/Compose simple; do not invent a Kubernetes-scale reconciler without a demonstrated need.

## Verification boundary

Do not claim Caddy, DNS, TLS, production SEO, external uptime, server restart recovery or end-to-end auto-deployment was verified by an image producer's local CI.

Document the concrete external prerequisites for the controller and give it a minimal consumer contract, not an implementation of the controller.
