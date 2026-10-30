# Portfolio Deployment

[English](README.md) · [Русский](README.ru.md)

Portfolio Deployment is Danil Miftakhov's deployment platform for Docker
applications on a single Ubuntu VPS. It connects signed releases in GitHub and
GHCR to controlled rollouts, health checks, HTTPS routing and recovery.

**[Author's portfolio → danil-miftakhov.dev](https://danil-miftakhov.dev/en/)**

[![CI](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml)
[![Python](https://img.shields.io/badge/Python-CLI-3776AB?logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://www.docker.com/)
[![Caddy](https://img.shields.io/badge/Caddy-HTTPS-1F88C0)](https://caddyserver.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- Application manifests in Git with schema validation and a separate admission policy.
- Offline deployment plans and Compose/Caddy previews before any server changes.
- Immutable GHCR images verified against signed release and source evidence.
- Policy-gated delivery with fresh host observations, scoped approvals and a host lock.
- Internal readiness checks and independent public HTTPS/content verification.
- SQLite deployment journal, last-known-good releases and constrained rollback/recovery.

## Technology stack

| Area | Technologies |
| --- | --- |
| Deployment engine | Python CLI, dataclasses, PyYAML, jsonschema |
| Containers and isolation | Docker Engine, Docker Compose, private networks, named volumes |
| Routing and TLS | Caddy, generated configuration, private admin socket |
| State and coordination | SQLite via sqlite3, filesystem receipts, Linux flock |
| CI and artifacts | GitHub Actions, GHCR, immutable image digests |
| Release trust and authorization | GitHub OIDC/Sigstore attestations, SSHSIG signatures, signed policies |
| Testing | pytest, pytest-xdist in CI, isolated Docker acceptance, Multipass VM E2E |
| Development | Git, GNU Make, Python 3.10+; CI uses Python 3.12.12 |

## Key engineering decisions

- **One engine across environments.** Offline mocks, disposable VMs and production
  adapters reuse the planner, executor and lifecycle rules.
- **Evidence before mutation.** Desired state in Git does not prove live state;
  stale observations, ownership conflicts or missing signatures block execution.
- **Recoverable operations.** Intent is recorded before mutation. Unfinished runs
  block further delivery until separately authorized recovery resolves them.
- **Separate responsibilities.** Application repositories build images; this
  project owns delivery and routing; server provisioning lives in a separate repo.

The project demonstrates infrastructure automation, Python system design,
software supply-chain verification and testing of deployment failure boundaries.
Module responsibilities and trust boundaries are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick start

Requires Git, GNU Make, Python with `venv` and pip. Docker, server access and
production credentials are unnecessary for local offline checks.

```sh
git clone https://github.com/DanilFaritovich/portfolio-deployment.git
cd portfolio-deployment
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make check PYTHON=.venv/bin/python
```

To inspect the registered application's Compose configuration without deploying:

```sh
make compose-render PYTHON=.venv/bin/python APPLICATION=project-router GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa COMPOSE_FORMAT=json
```

The SHA above is a synthetic example; replace it with the intended full commit SHA.
Planning against a server also requires an explicit observation snapshot.
See the [planning contract](docs/reconciliation-planning.md) and
[Compose engine guide](docs/docker-deployment-engine.md).

## Quality and testing

```sh
make fix PYTHON=.venv/bin/python
make validate PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
```

`fix` normalizes project text; `validate` checks the registry and admission policy;
`check` validates repository contracts and runs the offline pytest suite. CI adds
pinned actionlint workflow validation and two pytest workers. Separate Docker/VM
acceptance requires explicit opt-in and is never started by `make check`.

Recorded VM acceptance passed **15/15 scenarios**. Historical results and fixture
requirements are in the [local E2E guide](docs/local-e2e.md).

## Production delivery

[ProjectRouter](https://github.com/DanilFaritovich/project-router) is the first
registered application and the portfolio served at the website linked above.
Successful main-push CI hands its signed immutable release to the shared CD
workflow. The executor verifies policy, source, host state and readiness before
accepting the update. Manager releases have a separate delivery path.

The first rollout is complete; automatic application updates are confirmed through
2026-10-30. The latest reviewed Manager CD failed at remote intake; production
rollback acceptance remains unverified. Exact run links and evidence limits are in
[production status](docs/production-status.md).

Host provisioning belongs to
[portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
Production installation and recovery use separate operator procedures and authority.

## Limitations

- Designed for one VPS; high availability and zero downtime are not guaranteed.
- Current automated application delivery supports the narrow ProjectRouter
  image-only contract; other applications need declarations and admission review.
- Image rollback cannot reverse database migrations or replace backups.
- Offline checks do not establish current Docker, DNS, TLS or server health.

## Documentation

- [Architecture](ARCHITECTURE.md): components, flows and security boundaries.
- [Add an application](docs/application-acceptance.md) and [manifest contract](docs/manifest-v1.md).
- [Production status](docs/production-status.md): reviewed runs and delivery evidence.
- [Installation](docs/production-installation.md), [execution](docs/production-execution.md) and [operator approvals](docs/operator-approval.md).
- [Readiness and recovery](docs/readiness-recovery.md), [local E2E](docs/local-e2e.md) and [roadmap/history](docs/roadmap.md).
- [Contributor and agent rules](AGENTS.md); installed standards are recorded in the [standards lock](.codex-standards.lock.yaml).

## License

Code and technical documentation use the [MIT License](LICENSE), copyright 2026
Danil Miftakhov. Vendored development standards retain their
[upstream MIT notice](.agents/skills/UPSTREAM-LICENSE.txt).
