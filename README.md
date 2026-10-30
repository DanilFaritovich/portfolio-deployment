# Portfolio Deployment

[English](README.md) · [Русский](README.ru.md)

Portfolio Deployment is Danil Miftakhov's deployment platform for Docker
applications on a single Ubuntu VPS. It connects signed releases in GitHub and
GHCR to controlled rollouts, health checks, HTTPS routing and recovery.

**[Author's portfolio → danil-miftakhov.dev](https://danil-miftakhov.dev/en/)**

[![CI](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/offline.yml)
[![Manager CD](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/cd-deploy.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/portfolio-deployment/actions/workflows/cd-deploy.yml)
[![ProjectRouter CD](https://github.com/DanilFaritovich/project-router/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/DanilFaritovich/project-router/actions/workflows/deploy.yml)
[![Python](https://img.shields.io/badge/Python-CLI-3776AB?logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://www.docker.com/)
[![Caddy](https://img.shields.io/badge/Caddy-HTTPS-1F88C0)](https://caddyserver.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- Application manifests in Git with schema validation and a separate admission policy.
- Offline deployment plans and Compose/Caddy previews before any server changes.
- Immutable GHCR images verified against signed release and source evidence.
- Signed Manager installation bundles and a trusted Caddy image publisher.
- Policy-gated delivery with fresh host observations, scoped approvals and a host lock.
- Shared Caddy HTTPS routing, internal readiness checks and independent public
  HTTPS/content verification for application releases.
- SQLite deployment journal, last-known-good releases and constrained rollback/recovery.

## Technology stack

| Area | Technologies |
| --- | --- |
| Deployment engine | Python CLI, dataclasses, PyYAML, jsonschema |
| Containers and isolation | Docker Engine, Docker Compose, private networks, named volumes |
| Routing and TLS | Caddy, generated configuration, private admin socket |
| State and coordination | SQLite via sqlite3, filesystem receipts, Linux flock |
| CI and artifacts | GitHub Actions, workflow artifacts, GHCR, immutable image digests |
| Release trust and authorization | GitHub OIDC/Sigstore attestations, SSHSIG signatures, signed policies |
| Testing | pytest, pytest-xdist in CI, isolated Docker acceptance, Multipass VM E2E |
| Development | Git, GNU Make, Python 3.10+; CI uses Python 3.12.12 |

## Key engineering decisions

- **One engine across environments.** Offline mocks, disposable VMs and production
  adapters reuse the planner, executor and lifecycle rules. Operations run as
  one-shot CLI processes.
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

This is a command-line deployment tool, with no web interface to open on localhost.
Start with the offline walkthrough below; it validates the registry and produces
plans/configuration without Docker or server credentials. Real deployments are
covered under [Running deployments](#running-deployments).

### 1. Install local tools

Use Python **3.10+** with `venv` and pip, Git and GNU Make. CI uses Python 3.12.12.
For an Ubuntu 24.04 workstation:

```sh
sudo apt-get update
sudo apt-get install --yes git make python3 python3-venv python3-pip
```

### 2. Clone and prepare the environment

```sh
git clone https://github.com/DanilFaritovich/portfolio-deployment.git
cd portfolio-deployment
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make validate PYTHON=.venv/bin/python
```

Run subsequent commands from this repository directory. Virtualenv activation is
unnecessary: commands explicitly use `.venv/bin/python`. Validation should print
`OK: registry validated` for the included ProjectRouter manifest. If `venv` or
`make` is missing, complete step 1 before continuing.

### 3. Generate your first deployment plan

```sh
make plan PYTHON=.venv/bin/python OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION="$(git rev-parse HEAD)" PLAN_FORMAT=text
```

The included snapshot represents a synthetic empty environment. Against today's
registry, the plan reports `changes`, application/component creation and a route
change, with `executed=false`. Nothing is deployed. For your own analysis, replace
`OBSERVED` with a validated snapshot in the [planning contract](docs/reconciliation-planning.md)
format; this example does not discover your computer or production server.

### 4. Inspect the generated Compose configuration

```sh
make compose-render PYTHON=.venv/bin/python APPLICATION=project-router GIT_REVISION="$(git rev-parse HEAD)" COMPOSE_FORMAT=yaml
```

The terminal should show a Compose document with a `web` service, its pinned GHCR
image, security settings and networks. This is a preview, not a standalone
`docker compose up` setup: the proxy network and runtime prerequisites belong to
the managed deployment. See the [Compose engine guide](docs/docker-deployment-engine.md).
Use `make check PYTHON=.venv/bin/python` for the full offline suite; it can take
several minutes.

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
accepting the update. Manager releases have a separate delivery path that verifies
the installed bundle and health, retaining the previous tree for rollback.

The first rollout is complete; automatic application updates and Manager
Production CD are confirmed through 2026-10-30. The latest Manager attempt passed
with `no-op`: the requested version was already installed and passed health
verification, so no installation change was needed. Production rollback acceptance
remains unverified. Exact run links and evidence limits are in
[production status](docs/production-status.md).

Host provisioning belongs to
[portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
Production installation and recovery use separate operator procedures and authority.

## Running deployments

### Disposable local VM

After the local setup above, the guided E2E runner can exercise real deployments
in disposable Ubuntu VMs. It requires Linux amd64 with KVM, working/authenticated
Multipass and at least 20 GiB free on the home filesystem. First-run fixture
preparation can also require GHCR publication access. The target owner defaults
to the GitHub `origin` owner; set `E2E_FIXTURE_OWNER` to an owner you can publish
under when preparing your own images.

```sh
make e2e PYTHON=.venv/bin/python
```

The runner asks for separate confirmations before creating VMs, preparing images
and running Docker/Caddy operations. It uses VM Docker, not the host daemon.
Read the [E2E prerequisites and procedure](docs/local-e2e.md#prerequisites-and-topology)
before starting; retain the generated receipts and reports.

### Your own VPS

This is currently a deployment platform tailored to the author's infrastructure,
not a general-purpose installer. A clone and `.env` are insufficient for another
server: repository/domain identities, trusted workflows and host bindings need
reviewed adaptation in code and policy.

The setup sequence is:

1. Prepare Ubuntu 24.04 amd64, SSH access, Docker/Compose and DNS through the
   [server infrastructure project](https://github.com/DanilFaritovich/portfolio-server-infrastructure).
2. Review your application manifest/admission and source-signature requirements
   using the [application onboarding guide](docs/application-acceptance.md).
3. Prepare verified Manager/Caddy artifacts, actual host bindings, operator keys,
   the signed policy and CD capsule; configure protected GitHub Environments.
   See [installation](docs/production-installation.md) and the
   [current CD architecture](ARCHITECTURE.md#minimal-continuous-delivery--production-deployed).
4. Perform separately authorized installation, gateway bootstrap, image preparation
   and first rollout through the [bootstrap workflow](.github/workflows/production-bootstrap.yml).
5. Verify readiness and public HTTPS, then use signed release-driven CD for
   subsequent updates. See the [delivery flow](docs/production-status.md#what-happens-after-a-projectrouter-update).

Production guides include historical host values and preparation milestones;
they are reference procedures, not reusable approvals for a new VPS. A portable,
unattended installation path is not implemented.

## Limitations

- Designed for one VPS; high availability and zero downtime are not guaranteed.
- Current automated application delivery supports the narrow ProjectRouter
  image-only contract; other applications require acceptance, policy review and
  explicit CD integration.
- Image rollback cannot reverse database migrations or replace backups.
- Offline checks do not establish current Docker, DNS, TLS or server health.

## Repository structure

```text
registry/ + policy/ + schemas/   Application contracts and admission
scripts/                       Offline planning, Compose, gateway and lifecycle
scripts/production/            Read-only host diagnostics
scripts/production_integration/ Authenticated execution and recovery
scripts/cd/ + installation/    Delivery, sealed runtime and operator setup
tests/ + scripts/e2e/ + e2e/    Offline tests and disposable integration harnesses
.github/workflows/             CI, acceptance, signed publication and CD
```

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
