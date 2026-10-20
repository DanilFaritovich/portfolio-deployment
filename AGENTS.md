# Portfolio Deployment — agent instructions

Read this file first, then only skills relevant to the task. Read
[ARCHITECTURE.md](ARCHITECTURE.md) for architecture/deployment work; user-facing
setup is in [README.md](README.md) / [README.ru.md](README.ru.md).

## Scope and boundaries

Stage 01 provides documentation and offline development checks. Stage 02
implements the Service Registry contract, separate admission policy and bounded
offline manifest validator. Stage 03 adds immutable desired/observed models and
a pure offline reconciliation planner using explicit local snapshots. The real
registry and admission allowlist are empty pending reviewed applications and confirmed digests.
Stage 04 adds deterministic offline Caddy route/candidate previews and opt-in local
configuration validation; PR #4 is merged into develop. Stage 05 adds a Python CLI
Compose engine foundation, bounded Docker-only discovery, preflight, host flock,
scoped execution and offline mocks. Real operations are limited to separately
authorized disposable local smoke; it was not run during implementation. Full
live registry/gateway observation integration and production apply remain blocked.
Stage 05 PR #5 is merged into develop. Stage 06 adds offline readiness gates, a
SQLite journal/LKG, constrained rollback and read-only crash recovery analysis in
the same executor; Stage 06 PR #6 is merged into develop. Stage 07 adds a separate
Multipass Ubuntu 24.04 VM harness, real test-only adapters, readiness-gated loopback
Caddy activation and 15 real scenario definitions, reusing the same executor.
Real E2E and image builds are **not-performed**. Stages 08–09 remain **Planned**.
Server provisioning, Ubuntu, SSH, users, Docker Engine and host hardening belong
to `DanilFaritovich/portfolio-server-infrastructure`. Application repositories
own source, CI, image builds/publication and deployment contracts.

Do not implement Stages 08–09 without their own scoped task. Python CLI is selected;
SQLite via standard-library sqlite3 is the Stage 06 local journal, not live state.
Do not add a daemon, database server, Kubernetes
or deployment trigger without an explicit
architectural rationale and the relevant task scope.

## Repository map and standards

- Root documentation: architecture, bilingual READMEs and these instructions.
- [.codex-standards.lock.yaml](.codex-standards.lock.yaml): exact upstream,
  profile `docker-deployment-platform` v1, selections and package-file inventory.
- `.agents/skills/`: complete local standards; references are loaded on demand.
- `registry/applications/`, `policy/admission.yaml`: application manifests and
  separate trust allowlist; policy changes require security review.
- `schemas/`, `tests/fixtures/`: versioned application/admission schemas and
  synthetic positive/negative examples; fixtures are not production manifests.
- `scripts/`: offline tooling plus constrained Stage 05 Python CLI engine; no production apply interface.
- `scripts/compose.py`, `scripts/deployment.py`: immutable Compose/operations/results,
  resource preflight and scoped execution under host flock.
- `scripts/docker_adapter.py`, `scripts/docker_discovery.py`: bounded local-only
  commands, offline mock and deliberately incomplete registry observation provider.
- `scripts/deployment_cli.py`, `scripts/local_docker_smoke.py`: offline previews
  and separate disposable test lifecycle. See [Stage 05](docs/docker-deployment-engine.md).
- `scripts/validate_registry.py`: `validate_registry(root)` returns validated
  application documents or raises `RegistryValidationError`, without mutation
  or network access. Stage 03 consumes this API without duplicating admission.
  See [manifest v1](docs/manifest-v1.md) for the contract, limits and API details.
- `scripts/planning_inputs.py`: validated immutable input adapters;
  `scripts/reconciliation.py`: pure comparison engine; `scripts/plan_registry.py`:
  offline CLI. See [Stage 03](docs/reconciliation-planning.md) for snapshot schema,
  bounds, ownership, OCI limitations and exit codes. No real-state discovery.
- `scripts/gateway.py`: immutable routes/candidates, pure renderer and Stage 03 plan
  adapter; `scripts/gateway_cli.py`: preview CLI; `scripts/gateway_validation.py`:
  opt-in official local Caddy validation. See [Stage 04](docs/caddy-gateway.md).
  Desired routes never imply activation; retained candidates preserve active routes.
  Unknown/incomplete gateway scope yields no full Caddyfile. No reload exists.
- `scripts/readiness.py`, `scripts/composite_state.py`: bounded probe and complete-source
  contracts; real disposable VM transports are isolated in `scripts/e2e/`.
- `scripts/deployment_journal.py`, `scripts/deployment_lifecycle.py`: private SQLite
  transactions under host lock, intent before mutation, application-wide atomic LKG,
  conservative rollback and analysis-only crash recovery. See [Stage 06](docs/readiness-recovery.md).
  Unfinished runs block further journaled work; no reset/resume CLI or production apply.
- `scripts/e2e/`, `scripts/e2e_guest.py`, `e2e/images/`: VM-only integration,
  separate test admission/image lock, real readers/probes, guarded activation,
  scenario runner and non-destructive cleanup. See [Stage 07](docs/local-e2e.md).
- `Makefile`, `requirements-dev.txt`, `.editorconfig`: local check interface/setup.

Use local skills, not a fresh upstream download on each task:

- [task-development-workflow](.agents/skills/task-development-workflow/SKILL.md): scope, branches, validation and delivery.
- [standards-sync](.agents/skills/standards-sync/SKILL.md): compare locked revision, fetch only changed installed packages; preserve local adaptations and update inventory.
- [code-quality](.agents/skills/code-quality/SKILL.md): fixes before validation.
- [docker](.agents/skills/docker/SKILL.md): packaging and isolation when implemented.
- [github-actions](.agents/skills/github-actions/SKILL.md): offline PR checks when introduced.
- [continuous-delivery](.agents/skills/continuous-delivery/SKILL.md): separate foundation from production activation.
- [caddy-gateway](.agents/skills/caddy-gateway/SKILL.md): shared routing/TLS boundaries.
- [gitops-deployment](.agents/skills/gitops-deployment/SKILL.md): registry, planning and safe reconciliation.
- [documentation](.agents/skills/documentation/SKILL.md): accurate status, EN/RU alignment, architecture maintenance.
- [licensing](.agents/skills/licensing/SKILL.md): preserve upstream notice; do not choose a project license silently.

Optional `ansible`, `python-testing`, `structured-logging` are not installed.
Install only for an active requirement through `standards-sync`. Keep standards
changes separate from unrelated implementation work.

## Workflow and checks

Inspect Git status and branches before edits; preserve unrelated work. `main`
is stable; `develop` integrates development. New task branches start strictly
from `develop`. Never force-reset, rebase, discard changes or overwrite origin.
If `develop` is absent, establish it from `main` before creating a task branch.

Use the existing Makefile as the main interface. Default checks/previews are offline:
`make fix` normalizes project-owned text, preserving vendored skills;
`make validate` performs read-only validation of the complete registry and
admission policy; `make check` runs bootstrap checks, registry validation and
Stage 02–07 offline tests. `make plan OBSERVED=<local.json> GIT_REVISION=<full-sha>`
requires explicit offline inputs; blocked/degraded plans are not executable.
`make gateway-render` requires the same inputs; JSON separates desired/current/retained
candidate. `make gateway-check` runs offline gateway tests; `make gateway-validate`
explicitly requires a local trusted Caddy binary and never downloads or reloads it.
`make compose-render` and `make deployment-plan` require explicit APPLICATION and
GIT_REVISION; the latter also requires OBSERVED. `make compose-check` is offline;
`make compose-validate` invokes installed Compose syntax validation without daemon
contact. `make local-docker-smoke` requires explicit endpoint, session, preloaded
images and disposable authorization; never run it automatically. No production
apply/deploy target exists. Do not fabricate complete/trusted Stage 03 snapshots
from Docker inventory. Runtime refs and unverified OCI mappings block execution.
`make e2e-check` is offline. `e2e-prepare`, `e2e-images`, `e2e-test`,
`e2e-diagnose` and `e2e-clean` have separate explicit opt-ins; never run them
implicitly. `e2e-status` is read-only Multipass metadata. No VM deletion, volume
removal, automatic recovery replay or production Caddy activation is available.
Run safe fixes before targeted checks, then `make check` once implementation
stabilizes. Documentation-only changes need targeted documentation
checks, without rerunning unchanged executable tests. No broader `verify`/`ci`
target exists through Stage 07; do not add empty targets. Install pinned
development dependencies as documented before checks. Checks must fail clearly
when dependencies or required assets are missing. Negative YAML fixtures are
excluded from bootstrap syntax parsing and covered by dedicated tests.

Add meaningful tests for new behavior and failure boundaries as implementation
arrives. Reject unsafe manifest inputs before mutation; cover no-op behavior,
partial failure, data preservation and bounded health checks in later stages.
Passing offline checks never proves live Docker, DNS, TLS or VPS availability.
Keep documentation truthful; synchronize both READMEs when requested and update
existing architecture documentation for material architecture changes.

After ordinary completed tasks, commit, push the working branch and create a PR
into `develop` are authorized unless the user sets a stricter boundary. Inspect
the staged diff and available GitHub CI. Never merge automatically. Merges,
tags/releases and production operations require separate explicit authorization.
Do not change other repositories as a side effect.

## Security and operating boundaries

Stage 01–06 work forbids VPS/SSH contact, production Ansible/containers, Caddy activation, DNS or
domain changes, requesting/creating production secrets and automatic deployments.
Stage 07 additionally permits only separately authorized disposable VM operations
through its own opt-ins; implementation/offline validation never authorizes a run.
Only explicitly authorized disposable local Docker smoke or VM E2E is allowed; default
checks never connect to Docker. Never use production project names for smoke or
remove preexisting volumes. Keep the fixed host lock inode; no unlink/prune/down.
Future production work also requires explicit operation authorization or an
approved narrowly scoped policy. No credentials in Git, manifests, logs or
images; reference server-side secrets. Untrusted PRs receive no production
credentials. Docker administrative access is a host trust boundary. Never
automatically delete persistent data. CI must remain deterministic and offline
after tool provisioning, with no production endpoints or privileged credentials.

## Context budget

Use `rg` and scoped reads; do not read entire large files, all references or
unrelated skills. Copy package assets without loading their contents. Use compact
status/diff summaries and bounded logs; inspect details for failures/risk areas.
Run safe auto-fixes before read-only checks. Do not repeat successful checks
unless subsequent changes affect their result. Do not refetch unchanged standards
or require byte equality with upstream; compatible local adaptations are allowed.
