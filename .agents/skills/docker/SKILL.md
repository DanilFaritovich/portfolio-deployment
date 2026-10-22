---
name: docker
description: Core Docker/Compose rules for reproducible packaging, safe runtime configuration, service isolation, health, and efficient validation. Load detailed references only for the affected infrastructure topic.
---

# Docker Standard

Package applications so development/deployment environments can start required services predictably with a documented command.

Prefer Docker Compose for multi-service orchestration when it fits the project.

## Core goals

- reproducible builds;
- minimal production images;
- explicit runtime configuration;
- no embedded secrets;
- clear service/network boundaries;
- health/readiness behavior;
- persistent state only where required;
- predictable startup/shutdown;
- efficient validation.

## Startup

Provide a stable entry point such as:

`docker compose up -d`

or a documented Make target.

Document development/production differences explicitly.

## Dockerfiles

Prefer:

- explicit supported base image versions;
- multi-stage builds when materially useful;
- cache-friendly dependency layers, with dependency manifests/lockfiles copied before frequently changing source;
- minimal runtime dependencies;
- non-root runtime by default for application containers, with documented exceptions when required;
- deterministic dependency installation from committed lockfiles where the ecosystem supports them.

Changing only application code should not normally invalidate dependency installation layers. Use a multi-stage build when it materially reduces the final image or removes build tools. Do not pursue cache efficiency at the expense of reproducibility or secret safety.

When authoring or modifying a Dockerfile, dependency installation, build context, or image build cache, read [references/dockerfile-build.md](./references/dockerfile-build.md).

Never bake secrets into images.

## Build context

Use `.dockerignore` to exclude irrelevant local/generated data such as Git metadata, virtualenvs, `node_modules`, caches, coverage, local env files, and unnecessary build outputs.

Do not exclude source/dependency metadata required for reproducible builds.

## Runtime configuration

Use environment variables, mounted configuration, or platform secrets.

Never commit real production secrets in Dockerfiles, Compose files, source, or image layers.

Provide safe example configuration where useful.

## Production exposure and networks

Production should expose only the public gateway/reverse proxy whenever practical; internal backend/database/cache services should remain internal.

For production routing, service networks, frontend/backend/database exposure, and Nginx edge behavior, read [references/production-networking.md](./references/production-networking.md).

For a statically built/SSG site packaged as an independent container behind a shared gateway, read [references/static-site-runtime.md](./references/static-site-runtime.md). Verify actual container HTTP responses, locale paths, 404 behavior, assets, and the separation between build-time public metadata and runtime state.

## Redis

When Redis is required for distributed quota/cache state, keep it internal, health-checked, and explicitly configured.

For Redis/rolling-quota Docker details, read [references/redis-quota.md](./references/redis-quota.md).

## State, health, and migrations

Use persistent volumes only for state that must survive container replacement.

Define meaningful health/readiness checks and explicit startup dependencies.

Run required schema migrations before backend traffic is served.

For detailed volume/readiness/migration flow, read [references/migrations-health.md](./references/migrations-health.md).

## Validation

Use the smallest useful Docker validation for the changed scope.

Do not rebuild the entire stack when only one service/image requires validation.

Successful validation should be summary-first; detailed build/container output belongs primarily to failures.

For Docker command/log/CI behavior, read [references/validation-output.md](./references/validation-output.md).

## Security baseline

At minimum:

- no embedded secrets or Docker socket mounts for application containers by default;
- minimal published ports and internal backend/database/cache networking where practical;
- supported base images and minimal runtime packages;
- run application processes as non-root by default;
- drop unneeded Linux capabilities and prevent privilege escalation where supported;
- restrict writable paths and runtime resources when compatible with the service;
- explicit readiness behavior.

Do not use `privileged: true` or unrestricted host namespaces as a convenience workaround. Apply least-privilege settings per service; databases, proxies, and third-party images may need justified exceptions.

When authoring or changing Dockerfile/Compose runtime permissions, capabilities, filesystems, mounts, or resource limits, read [references/container-security.md](./references/container-security.md). For host-level Docker permissions, also use the Ansible Docker-host reference if applicable.
