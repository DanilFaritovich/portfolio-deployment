# Docker Health, State, and Migrations Reference

Read this reference when changing persistent volumes, service readiness, startup ordering, or database migrations.

## Volumes

Use persistent volumes only for state that must survive container replacement, such as PostgreSQL data or intentionally persistent application storage.

Do not mount source-code volumes in production unless the deployment model explicitly requires it.

## Health checks

Define meaningful health checks for long-running services.

A backend health endpoint should verify enough readiness to be useful without expensive work.

Do not use health checks that always succeed regardless of application state.

Use health/readiness-aware startup dependencies where appropriate.

## Database migrations

Run schema migrations before the backend begins serving production traffic.

Preferred sequence:

```text
build
-> start required database
-> run migration step/job
-> start backend
-> start/verify gateway
-> health validation
```

Do not hide failing migrations inside endless application restart loops.
