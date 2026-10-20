# Docker Validation and Output Reference

Read this reference when adding/running Docker validation, debugging container failures, or changing Docker-related CI.

## Lightweight validation

Prefer the smallest useful checks:

- `docker compose config` for configuration;
- targeted image build for changed services;
- health validation after startup.

Do not rebuild every image after changes that cannot affect image contents.

Expensive full build/start/health validation may run in CI.

## Summary-first output

Use compact Docker/Compose output during agent-driven checks.

Suppress or capture routine successful:

- dependency-install progress;
- package-download progress;
- BuildKit progress;
- full container logs.

A successful check should ideally report only meaningful outcomes:

```text
Compose config: OK
Redis: healthy
Backend: healthy
Gateway smoke: passed
```

Project Make/scripts may capture verbose output internally and:

- print a concise summary on success;
- reveal only the relevant captured diagnostic section on failure.

## Failure inspection

When a service fails:

1. identify the failing service/step;
2. inspect only its recent relevant logs;
3. expand scope/verbosity only if needed.

Avoid full `docker compose logs` for the whole stack unless the failure genuinely spans multiple services.

## CI relationship

Local workflow should use targeted Docker checks.

GitHub CI may perform:

- complete image builds;
- Compose validation;
- migration startup checks;
- Redis health/readiness;
- service health checks;
- gateway configuration validation;
- rate-limit smoke tests;
- selected E2E.

Do not use repeated full Docker logs as the normal local debugging loop.
