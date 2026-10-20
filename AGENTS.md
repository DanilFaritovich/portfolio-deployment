# Portfolio Deployment — agent instructions

Read this file first, then only skills relevant to the task. Read
[ARCHITECTURE.md](ARCHITECTURE.md) for architecture/deployment work; user-facing
setup is in [README.md](README.md) / [README.ru.md](README.ru.md).

## Scope and boundaries

This repository will manage a central service registry, immutable GHCR images,
Docker Compose reconciliation and a shared Caddy gateway on one VPS. These are
**Planned**. Stage 01 provides documentation and offline development checks only.
Server provisioning, Ubuntu, SSH, users, Docker Engine and host hardening belong
to `DanilFaritovich/portfolio-server-infrastructure`. Application repositories
own source, CI, image builds/publication and deployment contracts.

Do not implement Stages 02–09 in a bootstrap task. Do not select a controller
language, daemon, database, Kubernetes or deployment trigger without an explicit
architectural rationale and the relevant task scope.

## Repository map and standards

- Root documentation: architecture, bilingual READMEs and these instructions.
- [.codex-standards.lock.yaml](.codex-standards.lock.yaml): exact upstream,
  profile `docker-deployment-platform` v1, selections and package-file inventory.
- `.agents/skills/`: complete local standards; references are loaded on demand.
- `scripts/`: offline development tooling only; Python here does not select the controller runtime.
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

Use the existing Makefile as the main interface: `make fix`, targeted checks,
then `make check` once changes stabilize. Both commands are offline. No broader
`verify`/`ci` target exists at Stage 01; do not add empty targets. Install the
pinned development dependency as documented before checks. `make fix` only
normalizes project-owned text, preserving vendored skills. Checks must fail
clearly when dependencies or required assets are missing.

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

Bootstrap forbids VPS/SSH contact, production Ansible, containers, Caddy, DNS or
domain changes, requesting/creating production secrets and automatic deployments.
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
