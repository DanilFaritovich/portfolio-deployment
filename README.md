# Portfolio Deployment

[Русская версия](README.ru.md)

Central Docker/GitOps deployment platform for independently built portfolio
applications on a single Ubuntu VPS. **Current status: Stage 02 — Service Registry
& Manifest Validation.** This branch provides a versioned application contract,
separate admission policy and bounded offline validation. Runtime deployment is
**Planned**: no controller, gateway or applications are deployed here. The real
registry and admission allowlist are empty pending confirmed artifacts and review.

## Purpose and architecture

The target flow is application CI → GHCR immutable image → approved desired state
in Git → reconciliation → Docker Compose → health verification → Caddy routing.
Every runtime component in that flow is **Planned** here.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  owns Ubuntu, SSH/users, Docker Engine and host security.
- **Portfolio Deployment** implements registry contracts and offline admission;
  future stages will add versioned desired-state planning,
  digest selection, Compose execution, shared proxy networking, domains/HTTPS,
  health gates and deployment-result history.
- Application repositories such as
  [project-router](https://github.com/DanilFaritovich/project-router) own source,
  tests/CI, image builds and GHCR publication, and supply a deployment contract.
  Their current deployability is not established by offline validation.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, flows, trust boundaries
and recovery constraints.

## Planned capabilities and technologies

**Implemented offline:** closed application/admission schemas, immutable component
image references, cross-application validation and positive/negative tests. See
[manifest v1](docs/manifest-v1.md) for fields, policy, limits and the Stage 03 API.

**Planned:** immutable `ghcr.io/<owner>/<image>@sha256:<digest>` rollouts; deterministic
desired/actual plans and no-op reconciliation; shared Caddy HTTPS gateway;
isolated private dependencies; bounded health checks; scoped updates; recorded
last-known-good state and recovery outcomes.

Git, Docker Engine/Compose, GHCR and Caddy are selected target technologies.
The controller language, execution model and journal format are undecided.
Python, PyYAML, jsonschema and pytest serve offline validation only. No controller, daemon,
database or Kubernetes stack is selected or implemented.

## Repository structure

```text
.agents/skills/                 Installed skills, references, upstream MIT notice
.codex-standards.lock.yaml      Upstream revision, profile and package inventory
.editorconfig / .gitignore     Text conventions and local/secret exclusions
AGENTS.md                      Codex workflow and operating boundaries
ARCHITECTURE.md                 Planned runtime architecture
README.md / README.ru.md        English / Russian entry points
docs/roadmap.md                 Preliminary stages and decision gates
scripts/check_bootstrap.py      Offline repository validation and safe text fixes
scripts/validate_registry.py    Bounded offline validator and reusable input API
registry/applications/         Empty real registry; one manifest per application
policy/admission.yaml           Empty reviewed allowlist
schemas/                       Versioned closed application/admission contracts
tests/fixtures/                 Synthetic examples and hostile YAML inputs
requirements-dev.txt           Pinned offline validation tools
Makefile                       fix / validate / check interface
```

Compose generation and runtime configuration are deferred; there are no
deployable Compose or Caddy files. Synthetic fixtures are not production manifests.

## Local development and checks

Requirements: Git, GNU Make, Python 3.10+ with `venv` and pip. Docker, VPS access,
domains and production credentials are unnecessary. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make fix PYTHON=.venv/bin/python
make validate PYTHON=.venv/bin/python
make check PYTHON=.venv/bin/python
```

Dependency provisioning may use the network; all Make targets run offline.
`fix` normalizes line endings, final newlines and trailing whitespace in
project-owned text, preserving vendored skills. `check` is read-only: it checks
text conventions, YAML syntax/duplicate keys, standards-lock metadata, complete
package-file inventory/frontmatter, local Markdown links/anchors and README
structure/roadmap parity, then validates the complete registry and runs pytest.
`validate` checks only registry/admission. Negative YAML fixtures are excluded from
bootstrap syntax parsing and covered by dedicated tests. Translation accuracy also
needs human review; checks cannot prove language equivalence. External URLs are
not fetched.

No GitHub Actions workflow is installed through Stage 02; offline CI is Planned in
Stage 08. No `verify`, `ci` or deployment targets exist yet. Passing these checks
does not demonstrate live Docker, HTTPS, reconciliation or rollback.

## Development standards and Git delivery

Start with [AGENTS.md](AGENTS.md). Installed profile:
**docker-deployment-platform v1**, from
[codex-development-standards](https://github.com/DanilFaritovich/codex-development-standards)
at `c98740e6cc66fa20da0c81c6f394dd9a2b03013a` (`develop` source).

Required local skills: `task-development-workflow`, `standards-sync`, `code-quality`,
`docker`, `github-actions`, `continuous-delivery`, `caddy-gateway`,
`gitops-deployment`, `documentation`, `licensing`. Full packages include their
references; load references only for the active task. Optional `ansible`,
`python-testing` and `structured-logging` are not installed.

The [standards lock](.codex-standards.lock.yaml) records provenance and package
inventory. Future updates use local `standards-sync` and changed-file comparisons,
not full upstream downloads on every task. Compatible local adaptations are
allowed; inventory validation does not require upstream byte equality.

`main` is stable, `develop` integrates work, and task branches start strictly from
`develop`. Completed tasks may be committed, pushed and proposed by PR to
`develop`, unless the user sets a stricter boundary. Review and merge remain
separate: no automatic merge, tags/releases or production operations.

## Roadmap

The preliminary [roadmap](docs/roadmap.md) can change after architectural review:

| Stage | Scope | Status |
| --- | --- | --- |
| Stage 01 | Project Bootstrap | Implemented; PR #1 merged |
| Stage 02 | Service Registry & Manifest Validation | Implemented offline; review pending |
| Stage 03 | Desired State & Reconciliation Planning | Planned |
| Stage 04 | Caddy Gateway Foundation | Planned |
| Stage 05 | Docker Deployment Engine | Planned |
| Stage 06 | Health Checks, Rollback & Recovery | Planned |
| Stage 07 | Local E2E & Integration Testing | Planned |
| Stage 08 | GitHub Actions & Automation | Planned |
| Stage 09 | Production Integration & Release | Planned |

Stage 02 uses Python for offline tooling without selecting a controller runtime.
Real ProjectRouter registration requires a confirmed digest; no production
hostname is invented. Controller execution, production trigger/source, secret
delivery and journal/recovery details require later explicit decisions.

## Security, limitations and licensing

Bootstrap is local/GitHub only: no VPS/SSH, production Ansible or containers,
Caddy activation, DNS/domain changes, production secrets, automatic deployment,
other-repository modifications, merges or release tags. Future production work
needs separate authorization. PR checks must never receive production secrets.
Git contains configuration references, never real credentials. `.gitignore` is
only a safeguard; review staged files before committing.

The target single-host design does not promise high availability or zero downtime.
Shared proxy networks need lateral-access review. Image rollback cannot reverse
database migrations or replace data backups; persistent data must never be
automatically deleted.

No project license has been selected. Vendored standards retain their upstream
[MIT notice](.agents/skills/UPSTREAM-LICENSE.txt); this does not assign a license
to the rest of this repository. Choose a project license before release.
