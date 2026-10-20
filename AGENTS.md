# Portfolio Deployment — agent instructions

Read this file first, then only skills relevant to the task. Read
[ARCHITECTURE.md](ARCHITECTURE.md) for architecture/deployment work; user-facing
setup is in [README.md](README.md) / [README.ru.md](README.ru.md).

## Scope and boundaries

Stage 01 provides documentation and offline development checks. Stage 02
implements the Service Registry contract, separate admission policy and bounded
offline manifest validator with an API for Stage 03. The real registry and
admission allowlist are empty pending reviewed applications and confirmed digests.
Docker execution, Caddy, reconciliation, production deployment and all other
Stages 03–09 components remain **Planned**.
Server provisioning, Ubuntu, SSH, users, Docker Engine and host hardening belong
to `DanilFaritovich/portfolio-server-infrastructure`. Application repositories
own source, CI, image builds/publication and deployment contracts.

Do not implement Stages 03–09 without their own scoped task. Do not select a controller
language, daemon, database, Kubernetes or deployment trigger without an explicit
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
- `scripts/`: offline development tooling only; Python here does not select the controller runtime.
- `scripts/validate_registry.py`: `validate_registry(root)` returns validated
  application documents or raises `RegistryValidationError`, without mutation
  or network access. Stage 03 can consume this input API; planning is not implemented.
  See [manifest v1](docs/manifest-v1.md) for the contract, limits and API details.
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

Use the existing Makefile as the main interface. All targets are offline:
`make fix` normalizes project-owned text, preserving vendored skills;
`make validate` performs read-only validation of the complete registry and
admission policy; `make check` runs bootstrap checks, registry validation and
Stage 02 tests. Run safe fixes before targeted checks, then `make check` once
implementation stabilizes. Documentation-only changes need targeted documentation
checks, without rerunning unchanged executable tests. No broader `verify`/`ci`
target exists through Stage 02; do not add empty targets. Install pinned
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

Stage 01–02 work forbids VPS/SSH contact, production Ansible, containers, Caddy, DNS or
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
