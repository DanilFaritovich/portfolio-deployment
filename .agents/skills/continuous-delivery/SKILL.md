---
name: continuous-delivery
description: Continuous Delivery conventions for Docker Compose projects using GitHub Actions, GHCR, immutable images, GitHub Environments, server runtime configuration, migrations, health checks, and rollback. Route to the foundation phase when preparing a project before a server exists, and to production integration when VDS/GitHub production prerequisites are ready.
---

# Continuous Delivery Standard

Use this standard for Dockerized applications when Docker Compose is sufficient.

Do not introduce Kubernetes only to implement CD.

## Core deployment model

Preferred production flow:

```text
Pull Request
  -> CI
  -> merge to stable production source
  -> build production images
  -> push immutable images to registry
  -> deploy exact image versions
  -> migrations
  -> update services
  -> health checks
  -> success
```

Production deployment should normally start only from `main`, a release/tag, or another explicitly approved stable source.

Do not deploy arbitrary feature branches to production.

## Build once, deploy the same artifact

Build application images in GitHub Actions and publish them to the configured registry, normally GHCR for GitHub-hosted portfolio projects.

Do not SSH to the server and rebuild application source there.

Preferred:

```text
GitHub Actions
  -> docker build
  -> GHCR
  -> VDS pulls exact image
```

Production must run the same artifact produced by the deployment workflow.

## Immutable production state

Every deployment must identify the exact application image.

Prefer immutable digests:

```text
ghcr.io/<owner>/<image>@sha256:<digest>
```

A commit-SHA tag may also be published for readability.

Do not rely only on mutable tags such as `latest` for production state or rollback.

The deployed Git revision and image digests must be discoverable.

## Repository vs runtime state

Version-control safe deployment artifacts such as:

- `compose.production.yml`;
- Dockerfiles;
- reverse-proxy configuration;
- deployment scripts;
- Makefile targets;
- GitHub Actions workflows;
- migrations;
- `.env.example`;
- deployment documentation.

Never commit:

- production `.env`;
- passwords/API keys/tokens;
- private SSH keys;
- private certificates;
- database credentials.

Server-side runtime configuration is deployment state, not repository state.

## Production topology

Expose only the public gateway when practical.

Typical topology:

```text
Internet
  |
  v
Nginx / Caddy
  |
  +--> frontend
  |
  +--> /api/ -> backend
                  |
                  +--> PostgreSQL / SQLite volume
                  +--> Redis
```

Backend, databases, and Redis should normally remain internal and must not publish production host ports without a concrete need.

Preserve the project's chosen reverse proxy. Do not replace Nginx with Caddy or vice versa merely because an example differs.

## Phase routing

Continuous Delivery is implemented in two phases so project preparation does not depend on production credentials or a live server.

### Phase 1: foundation

Use [references/foundation.md](./references/foundation.md) when the task is to:

- prepare a project for CD;
- add production Compose/build/publish artifacts;
- define deployment scripts/Makefile commands;
- define the VDS/GitHub Environment contract;
- validate deployment artifacts without a live production server.

This phase must not require real production secrets, SSH access, or an existing VDS.

### Phase 2: production integration

Use [references/production-integration.md](./references/production-integration.md) when:

- the VDS exists;
- required GitHub Environment secrets/variables can be configured;
- SSH and runtime deployment prerequisites are ready;
- the task is to complete, exercise, or harden end-to-end production deployment.

Do not redesign a valid foundation during integration unless a concrete incompatibility is found.

## Image publishing without application-owned production deployment

When a service repository owns only image build/publish and a separate deployment-controller repository owns Caddy, VPS rollout and service reconciliation, read [references/image-producer.md](./references/image-producer.md). This is an image-producer foundation, not a completed end-to-end CD system. Do not require the application to contact the server or maintain the shared gateway.

## Context loading

Load only the reference for the active phase, plus the image-producer reference when this separation of responsibilities actually applies.

Do not read both phase references merely because the project eventually needs both.

If a user asks for end-to-end CD but production prerequisites do not yet exist:

1. complete the foundation phase;
2. document the exact external prerequisites;
3. stop before pretending that production integration was verified.

If a task genuinely crosses both phases and the required external prerequisites already exist, load both references in order: foundation assumptions first, then production integration.

## Common safety rules

- use least-privilege GitHub Actions permissions;
- require the exact production revision to pass its required validation before deployment can start;
- keep production secrets scoped to a GitHub Environment;
- never expose production secrets to untrusted Pull Request jobs;
- allow only one production deployment at a time;
- keep migrations explicit;
- use bounded readiness/health waits;
- do not report success before required health verification passes;
- do not assume application image rollback reverses database/persistent state;
- prefer a dedicated non-root deployment user over root SSH;
- keep deployment logic maintainable and version-controlled.

## CI/CD boundary

Pull Request CI validates deployment artifacts but must not perform a real production deployment.

Production deployment must be gated on successful validation for the exact revision being deployed. Do not run CI and production deployment as independent parallel workflows where deployment can proceed while that revision's required checks are failing or still running.

The protected production job runs only from the approved production source and environment.

## Completion

CD is complete only after both phases are satisfied:

```text
foundation ready
  -> external VDS/GitHub prerequisites configured
  -> production integration verified
  -> controlled deployment succeeds
  -> health verification succeeds
  -> rollback/failure behavior is defined
```

Do not mark production CD complete based only on generated YAML or an unexecuted deployment path.
