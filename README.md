# Portfolio Deployment

[Русская версия](README.ru.md)

Central Docker/GitOps deployment platform for portfolio applications on one Ubuntu
VPS. **Production server deployment is complete, including the first ProjectRouter
rollout (operator confirmation on 2026-10-28).** Automatic ProjectRouter updates
are confirmed by successful production CD runs on 2026-10-29 and 2026-10-30.
Manager CD succeeded earlier; its latest reviewed run on 2026-10-29 failed at
remote intake. These are separate delivery paths.

[Current status and evidence](docs/production-status.md) records the reviewed
`main` history, CI/release/CD links and evidence limits. Earlier Stage 01–09/C3/C4
documents describe their preparation milestones; they do not mean that the host
still awaits first installation. Stage 07 previously passed 15/15 real VM E2E
scenarios; the separate Stage 05 local smoke was not performed.

ProjectRouter is registered in the [registry](registry/applications/project-router.yaml)
and [admission policy](policy/admission.yaml). After successful CI for a push to
`main`, its workflow calls shared production CD at a pinned Manager revision.
Automatic delivery verifies the immutable digest, signed release claims, standing
policy, fresh host state, readiness and public HTTPS/content. The Environment may
require manual approval. [ProjectRouter CD 41065912995](https://github.com/DanilFaritovich/project-router/actions/runs/41065912995)
completed successfully on 2026-10-30, including the application update execution
and independent HTTPS witness step. Policy and checks can block future delivery;
a production rollback drill remains unverified in this review.

Contracts and procedures: [manifest v1](docs/manifest-v1.md),
[adding an application](docs/application-acceptance.md),
[production installation](docs/production-installation.md),
[execution/authorization](docs/production-execution.md) and
[operator SSHSIG](docs/operator-approval.md). Historical
[ProjectRouter acceptance](docs/project-router-acceptance.md),
[Caddy acceptance](docs/caddy-runtime-acceptance.md) and
[C3 release review](docs/protected-main-release.md) retain their original evidence.
New production operations require their applicable authorization or an existing
narrow signed policy; deployment completion does not grant new authority.

## Purpose and architecture

The operational flow is application CI → immutable GHCR image → release/policy
verification → host-local executor → Docker Compose → readiness → Caddy and public
HTTPS. The registry and offline planner define the contract; live adapters and CD
verify facts before execution. Production integration reuses the same executor,
SQLite journal/LKG and host lock as offline/VM validation.

- [portfolio-server-infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure)
  owns Ubuntu, SSH/users, Docker Engine and host security.
- **Portfolio Deployment** owns admission, desired state, planning, Compose,
  production adapters, Caddy, readiness, the journal and policy-gated CD.
- Application repositories such as
  [project-router](https://github.com/DanilFaritovich/project-router) own source,
  tests/CI, image builds and GHCR publication, and supply a deployment contract.
  Their current deployability is not established by offline validation.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, flows, trust boundaries
and recovery constraints.

## Capabilities and technologies

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

Production execution, live adapters, Caddy and CD are implemented. **Planned:**
production rollback acceptance and automation beyond the current narrow
ProjectRouter image-only contract. No zero-downtime claim.

Technologies: Git, Docker Engine/Compose, GHCR, Caddy and a Python CLI using
PyYAML/jsonschema. The journal uses standard-library SQLite; no database server,
daemon or Kubernetes is used. The one-shot executor checks exact candidates,
signatures, source, fresh observations and one-use nonces under the canonical lock.

Manager owns its installation, private storage, Caddy and application networks;
Server Infrastructure owns host preparation and maintenance. Installation, image
preparation, `first-bootstrap` and `first-project-router-rollout` are separate
operation scopes. Ordinary offline commands never rerun them. After first rollout,
image updates use CD; bootstrap and recovery retain separate authority.
See [status and delivery flow](docs/production-status.md).

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
scripts/production/            Read-only host inspection and private reports
scripts/production_integration/ Authenticated observations, execution and recovery
scripts/cd/                    Policy-gated Manager and application delivery
installation/                  Sealed launchers, CD capsule and operator setup
.github/workflows/             Offline CI, isolated acceptance, release and CD
registry/applications/         ProjectRouter registered; one manifest per application
policy/admission.yaml           ProjectRouter eligibility; security review required
schemas/                       Versioned closed application/admission contracts
tests/fixtures/                 Synthetic examples and hostile YAML inputs
requirements-dev.txt           Pinned offline validation tools
requirements-offline-ci.txt    Additional pinned parallel pytest tooling for CI
Makefile                       fix / validate / check / plan / gateway-* interface
```

Compose and production Caddy are implemented; runtime configuration remains
server-side state. Synthetic fixtures are not production manifests.

## Local development and checks

Requirements: Git, GNU Make, Python 3.10+ with `venv` and pip (CI uses Python
3.12.12). Docker, VPS access,
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

Replace APP with an admitted application, currently `project-router`. Preview JSON
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
requires fresh discovery. This low-level result alone does not establish readiness or release success.

Stage 08 adds offline GitHub Actions for PRs into `develop` and `main`: pinned
Python/tools, pip caching, minimal read permissions, `make check` and
`make workflow-check` (actionlint). No `make ci` alias is needed. These offline PR
checks do not run VM/E2E, Docker, publication or deployment;
release/CD workflows are separate. Configure the required
**Offline CI / Offline checks** check in GitHub branch rules manually.
CI installs `requirements-offline-ci.txt` and runs pytest with two workers;
local setup above uses the sequential suite. Separate integration workflows can
run disposable Docker acceptance; they are not part of offline `make check`.
See [Stage 08](docs/stage-08-automation.md) for tool setup and the reviewed manual
procedure to export/verify successful-session evidence before separately approved
VM stop/deletion. Stage 08 did not execute this procedure; current VM existence is not asserted here.
No `verify`, deploy or production apply target exists. Passing offline checks
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
separate: no automatic merge or tags/releases; production operations follow their own policy.

## Roadmap

The preliminary [roadmap](docs/roadmap.md) can change after architectural review:

| Stage | Scope | Status |
| --- | --- | --- |
| Stage 01 | Project Bootstrap | Implemented; PR #1 merged |
| Stage 02 | Service Registry & Manifest Validation | Implemented offline; PR #2 merged |
| Stage 03 | Desired State & Reconciliation Planning | Implemented offline; PR #3 merged |
| Stage 04 | Caddy Gateway Foundation | Implemented offline; PR #4 merged into develop |
| Stage 05 | Docker Deployment Engine | Implemented foundation; PR #5 merged; smoke not-performed; foundation boundary; production through Stage 09 |
| Stage 06 | Readiness, Journal & Recovery | Implemented offline; PR #6 merged; SQLite/LKG and analysis-only recovery |
| Stage 07 | Local E2E & Integration Testing | Implemented; real local VM E2E passed, 15/15; historical local acceptance |
| Stage 08 | GitHub Actions & Automation | Implemented offline; manual VM completion procedure awaiting live review |
| Stage 09 | Production Integration & Release | First rollout complete; automatic ProjectRouter updates confirmed through 2026-10-30; latest reviewed Manager CD failed at remote intake; live rollback acceptance pending |

Stage 05 selects Python CLI without changing Stage 02–04 contracts. Stage C2
registered ProjectRouter with its independently verified digest. Stage 06 owns
readiness, rollback, journal and recovery; Stage 07 expands isolated E2E.
Production adapters and policy-gated CD are implemented over the same engine.

SQLite durability requires reliable local storage/fsync. The journal is not live
state or a backup. Unfinished runs block new work; recovery needs its own signed
authority and never automatically replays mutations.
See [Stage 06 limits and APIs](docs/readiness-recovery.md).

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
All six fixture images (**6/6**) were built, published to GHCR and anonymously verified by
immutable digest in fixture session `5b780834b80ffe9e`; later E2E attempts reused them.
`make e2e-report` reads private local session receipts; `e2e-diagnose` reads the VM.
The CLI is `python -m scripts.e2e.cli`; separate prepare/images/test/status/clean
commands remain available. Failed/unknown runs block another orchestration in the
same state directory, retain all evidence and are never resumed or cleaned up
automatically. `make e2e-check` and `make check` remain fully offline.
**Verified local acceptance (2026-10-21):** session `4963fe050c4567f2`, receipt
`complete/passed`, `realE2E=performed`, **15/15 real scenarios passed**; the accepted
implementation also passed **779 offline tests**. Production deployment was not
performed as part of Stage 07. Server deployment
completed later: see [current production status](docs/production-status.md).
Earlier failed attempts and their
closure evidence are retained; intentional crash/unknown journals remain
analysis-only, without replay. See [acceptance and history](docs/local-e2e.md#verified-local-acceptance).

## Security, limitations and licensing

Ordinary development and PR checks remain offline. Disposable VM E2E needs separate
opt-ins. New SSH/production operations, policy/key changes, bootstrap and recovery
require the applicable authorization or an existing narrow signed policy.
Deployment completion does not authorize other-repository modifications, merges
or release tags. PR checks must never receive production secrets.
Git contains configuration references, never real credentials. `.gitignore` is
only a safeguard; review staged files before committing.

The target single-host design does not promise high availability or zero downtime.
Shared proxy networks need lateral-access review. Image rollback cannot reverse
database migrations or replace data backups; persistent data must never be
automatically deleted.

Project code is licensed under the [MIT License](LICENSE), copyright 2026
Danil Miftakhov. Vendored standards retain their upstream
[MIT notice](.agents/skills/UPSTREAM-LICENSE.txt); third-party notices remain applicable.
