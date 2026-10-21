# Portfolio Deployment

[Русская версия](README.ru.md)

Central Docker/GitOps deployment platform for independently built portfolio
applications on a single Ubuntu VPS. **Current status: Stage 07 — Local E2E infrastructure (real E2E not-performed).**
Registry/admission, offline planning and retained Caddy previews are implemented;
Stage 04 PR #4, Stage 05 PR #5 and Stage 06 PR #6 are merged into `develop`. Stage 05 adds deterministic Compose,
bounded Docker discovery, preflight, locking and scoped execution with offline mocks.
Stage 06 adds bounded readiness, SQLite journal/LKG, constrained rollback and crash
recovery analysis in the same executor. Stage 07 adds a separate disposable Ubuntu
VM harness and real test-only observation/probe adapters; actual execution remains opt-in.
Production deployment remains **Planned**: full registry/gateway observations are
not available from Docker alone. No production apply interface exists. The real
registry/admission allowlist remain empty; local smoke was not performed.

## Purpose and architecture

The target flow is application CI → GHCR immutable image → approved desired state
in Git → reconciliation → Docker Compose → health verification → Caddy routing.
Real production integration and gateway activation remain **Planned**; readiness
and recovery have offline-tested contracts, with VM-only adapters in Stage 07.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  owns Ubuntu, SSH/users, Docker Engine and host security.
- **Portfolio Deployment** implements registry contracts, offline admission and
  explicit-snapshot planning, deterministic Compose and a constrained execution
  foundation. Future stages add complete runtime integration, domains/HTTPS,
  production observation/transport integration. Offline readiness and durable deployment
  history are implemented in Stage 06.
- Application repositories such as
  [project-router](https://github.com/DanilFaritovich/project-router) own source,
  tests/CI, image builds and GHCR publication, and supply a deployment contract.
  Their current deployability is not established by offline validation.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, flows, trust boundaries
and recovery constraints.

## Planned capabilities and technologies

**Implemented offline:** closed application/admission schemas, immutable component
image references, cross-application validation and positive/negative tests. See
[manifest v1](docs/manifest-v1.md) for fields, policy and validation limits.
The [Stage 03 planning contract](docs/reconciliation-planning.md) documents immutable
models, bounded snapshots, component updates, no-op, route changes, drift, health
and lifecycle/storage blockers. Planning never executes operations.
[Stage 04](docs/caddy-gateway.md) adds immutable gateway routes/candidates, fixed
Caddyfile rendering, separate desired/current/retained sets and opt-in official
local Caddy validation. Unconfirmed changes retain existing active routes;
unknown or untrusted gateway scope blocks a full candidate.

[Stage 05](docs/docker-deployment-engine.md) adds immutable Compose artifacts,
independent projects/private networks, named volumes, ownership/OCI preflight,
host flock and component-scoped execution. Unresolved refs, unsupported storage
changes, unknown/foreign resources and unverified image mapping block execution.
Docker-only discovery deliberately produces an incomplete Stage 03 snapshot.

[Stage 06](docs/readiness-recovery.md) adds bounded HTTP/worker readiness, private
SQLite intent/events, atomic application last-known-good, constrained stateless
image rollback, analysis-only crash recovery and composite observation contracts.
Stage 07 supplies test-only probes/readers; post-crash mutation remains unauthorized.

**Planned:** production rollouts, complete live reconciliation and shared Caddy
HTTPS. No zero-downtime claim.

Git, Docker Engine/Compose, GHCR and Caddy are selected target technologies.
Python CLI is selected for Stage 05, reusing PyYAML/jsonschema and pytest.
The journal uses standard-library SQLite; no database server, daemon or Kubernetes
is implemented. Protected production source/trigger remain undecided.

## Repository structure

```text
.agents/skills/                 Installed skills, references, upstream MIT notice
.codex-standards.lock.yaml      Upstream revision, profile and package inventory
.editorconfig / .gitignore     Text conventions and local/secret exclusions
AGENTS.md                      Codex workflow and operating boundaries
ARCHITECTURE.md                 Architecture and runtime boundaries
README.md / README.ru.md        English / Russian entry points
docs/roadmap.md                 Preliminary stages and decision gates
scripts/check_bootstrap.py      Offline repository validation and safe text fixes
scripts/validate_registry.py    Bounded offline validator and reusable input API
scripts/planning_inputs.py      Validated immutable desired/observed input adapters
scripts/reconciliation.py       Pure comparison engine and immutable plan
scripts/plan_registry.py        Explicit-input offline planning CLI
scripts/gateway.py              Pure routes, retained candidates and Caddyfile renderer
scripts/gateway_cli.py          Explicit-input offline gateway preview CLI
scripts/gateway_validation.py   Opt-in official local Caddy validation
scripts/compose.py              Immutable deterministic Compose artifact
scripts/deployment.py           Preflight, host flock, scoped operations/results
scripts/docker_adapter.py       Bounded local command adapter and offline mock
scripts/docker_discovery.py     Docker-only observations, incomplete registry provider
scripts/deployment_cli.py       Offline Compose and deployment previews
scripts/readiness.py            Bounded probe interfaces, no real transport
scripts/composite_state.py      Full-source observation contract
scripts/deployment_journal.py   Private versioned SQLite intent/events/LKG
scripts/deployment_lifecycle.py Readiness, constrained rollback and recovery analysis
scripts/local_docker_smoke.py   Separate opt-in disposable test lifecycle
registry/applications/         Empty real registry; one manifest per application
policy/admission.yaml           Empty reviewed allowlist
schemas/                       Versioned closed application/admission contracts
tests/fixtures/                 Synthetic examples and hostile YAML inputs
requirements-dev.txt           Pinned offline validation tools
Makefile                       fix / validate / check / plan / gateway-* interface
```

Compose generation is implemented; runtime config delivery and production Caddy
activation remain pending. Synthetic fixtures are not production manifests.

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

Dependency provisioning may use the network; default checks/previews run offline.
Official syntax validation and local smoke are separate opt-in commands.
`fix` normalizes line endings, final newlines and trailing whitespace in
project-owned text, preserving vendored skills. `check` is read-only: it checks
text conventions, YAML syntax/duplicate keys, standards-lock metadata, complete
package-file inventory/frontmatter, local Markdown links/anchors and README
structure/roadmap parity, then validates the complete registry and runs pytest.
`validate` checks only registry/admission. Negative YAML fixtures are excluded from
bootstrap syntax parsing and covered by dedicated tests. Translation accuracy also
needs human review; checks cannot prove language equivalence. External URLs are
not fetched.

Offline planning requires an explicit local snapshot and full Git SHA:

```sh
make plan PYTHON=.venv/bin/python OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa PLAN_FORMAT=json
```

The SHA is synthetic; supply the intended revision. No snapshot is assumed.
Unknown/incomplete evidence, ownership conflicts and existing storage/mount changes
block plans. Config/secret IDs do not prove unchanged contents; OCI index mapping
needs explicit evidence. Plans are never execution authorization. CLI exits are
0 for comparable changes/no-op, 1 for input errors, 2 for blockers, 3 for degraded
health; Make uses its own recipe-failure code. See the planning contract for API,
text output and future preflight/readiness obligations.

Gateway previews require the same explicit inputs:

```sh
make gateway-render PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa GATEWAY_FORMAT=json
make gateway-check PYTHON=.venv/bin/python
make gateway-validate PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

`GATEWAY_FORMAT=caddyfile` prints the retained full candidate. Empty sets use an
explicit no-routes outcome (JSON); no public fallback is generated. JSON separates
requested route changes from retained active routes. Gateway CLI exits: 0 successful
preview (including pending), 1 input error, 2 blocked, 3 no renderable Caddyfile,
4 requested official validation failed/not performed. Missing Caddy is reported
honestly; nothing is downloaded. Official validation checks syntax, never readiness
or activation permission. `make check` includes Stage 04 tests. See the gateway
document for APIs, trust boundaries and future reload/TLS prerequisites.

Stage 05 interfaces require explicit root, application and revision:

```sh
make compose-render PYTHON=.venv/bin/python REGISTRY_ROOT=. APPLICATION=APP GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa COMPOSE_FORMAT=json
make deployment-plan PYTHON=.venv/bin/python REGISTRY_ROOT=. APPLICATION=APP GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa OBSERVED=tests/fixtures/observed/noop.json
make compose-check PYTHON=.venv/bin/python
```

Replace APP with an admitted application; the real registry is empty. Preview JSON
is machine-readable and never authorizes execution. `compose-validate` with the
same root/application/revision optionally invokes installed Compose v2 `config -q`
without daemon contact; missing prerequisites report `not-performed`.

`local-docker-smoke` requires explicit SMOKE_ENDPOINT (canonical Unix socket),
SMOKE_SESSION (unused 16-hex token), two preloaded immutable GHCR manifest images
SMOKE_IMAGE_A/B with Engine Descriptor evidence, and
SMOKE_AUTHORIZE=disposable-local-docker. It is never invoked by `check`.
The [Stage 05 guide](docs/docker-deployment-engine.md) describes prerequisites,
isolation, scoped cleanup and incomplete live discovery. Real smoke was not run.
Compose success is `applied-unverified`; timeout/disconnect is `unknown` and
requires fresh discovery. Readiness and release success remain unverified.

No GitHub Actions workflow is installed through Stage 07; CI is Planned in Stage 08.
No `verify`, `ci`, deploy or production apply target exists. Passing offline checks
does not prove live Docker, HTTPS, reconciliation or rollback.

## Development standards and Git delivery

Start with [AGENTS.md](AGENTS.md). Installed profile:
**docker-deployment-platform v1**, from
[codex-development-standards](https://github.com/DanilFaritovich/codex-development-standards)
at `104a8ed8e10a07fbb9a9ac2d7be669cc93913590` (`develop` source).

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
| Stage 02 | Service Registry & Manifest Validation | Implemented offline; PR #2 merged |
| Stage 03 | Desired State & Reconciliation Planning | Implemented offline; PR #3 merged |
| Stage 04 | Caddy Gateway Foundation | Implemented offline; PR #4 merged into develop |
| Stage 05 | Docker Deployment Engine | Implemented foundation; PR #5 merged; smoke not-performed; production apply blocked |
| Stage 06 | Readiness, Journal & Recovery | Implemented offline; PR #6 merged; SQLite/LKG and analysis-only recovery |
| Stage 07 | Local E2E & Integration Testing | Infrastructure implemented; real E2E not-performed |
| Stage 08 | GitHub Actions & Automation | Planned |
| Stage 09 | Production Integration & Release | Planned |

Stage 05 selects Python CLI without changing Stage 02–04 contracts. Real
ProjectRouter registration still requires a confirmed digest. Stage 06 owns
readiness, rollback, journal and recovery; Stage 07 expands isolated E2E. Full live
observation, protected production source, configuration delivery and index mapping
need review before production execution.

SQLite durability requires reliable local storage/fsync. The journal is not live
state or a backup. Unfinished runs block new journaled work; crash analysis never
replays mutations or clears uncertainty. Production still needs real coherent
Docker/gateway/runtime observations, protected source and operation authorization,
configuration delivery, artifact retention, backup/recovery ownership and live
acceptance. See [Stage 06 limits and APIs](docs/readiness-recovery.md).

## Disposable local E2E

`make e2e` handles first and repeated runs without environment exports. It derives
a lowercase GHCR owner from verified GitHub origin. Private state defaults to
`~/.local/share/portfolio-deployment/e2e-state/` and fixtures to
`~/.local/share/portfolio-deployment/e2e-fixtures/`, independently of the checkout
and current directory. Missing directories are safely created with mode `0700`;
private files use `0600`. Existing permissions are never changed; a `775` checkout
needs no permission changes. `E2E_STATE_DIR` and `E2E_FIXTURE_DIR` override the paths
with the same owner, permission, no-symlink and no-traversal checks. Nonempty legacy
`.e2e-state`/`.e2e-fixtures` directories block new commands pending
[manual review and transition](docs/local-e2e.md#legacy-state-transition); nothing
is automatically moved, deleted or reset.
Without a ready lock it offers preparation of six images, resolves the official
Python 3 source in `e2e/python-base.json` to a verified direct linux/amd64 digest,
and pins raw manifest/config metadata before building. Separate confirmations
remain mandatory for builder VM creation, builds, publisher VM creation, GHCR
publication, public verification, test VM creation, immutable pulls, Docker
mutations and loopback Caddy activation. Credentials stay only in the publisher VM.
With a verified ready lock, later runs skip build/publication. Linux amd64, KVM,
authenticated Multipass, network/package access and adequate VM resources are
required. Host Docker is unused; all `E2E_*` variables are optional overrides.

`make e2e-fixtures` prepares fixtures separately with the same defaults and
confirmations. CLI `fixtures --plan --base <immutable-reference>` and raw-manifest
import remain offline. Unknown outcomes preserve evidence and block retry; no
reset or cleanup runs automatically. See the [Stage 07 operator instructions](docs/local-e2e.md).
No digests are fabricated; real builds/publication remain **not-performed**.
`make e2e-report` reads private local session receipts; `e2e-diagnose` reads the VM.
The CLI is `python -m scripts.e2e.cli`; separate prepare/images/test/status/clean
commands remain available. Failed/unknown runs block another orchestration in the
same state directory, retain all evidence and are never resumed or cleaned up
automatically. `make e2e-check` and `make check` remain fully offline.
**Real E2E: not-performed.** No VM or real Docker/Caddy was run for this stage.

## Security, limitations and licensing

Stage 07 permits separately authorized disposable Ubuntu VM E2E, including test-only
loopback Caddy activation. There is no authorization for
VPS/SSH, production Ansible or containers,
production Caddy activation, DNS/domain changes, production secrets, automatic deployment,
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
