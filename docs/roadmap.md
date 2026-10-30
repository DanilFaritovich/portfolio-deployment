# Implementation roadmap and historical milestones

Current production review (2026-10-30): full server deployment and first
ProjectRouter rollout are complete per operator (2026-10-28). Automatic
ProjectRouter updates succeeded on 2026-10-29 and 2026-10-30. Manager Production CD
run 40948774870 attempt 2 succeeded on 2026-10-30 with `no-op`: the requested
Manager revision was already installed and passed health verification. Its first
attempt remains a historical failure. See [status, evidence and main history](production-status.md).
Live rollback acceptance still needs separate evidence. Minimal CD is implemented;
the earlier digest-update-PR design
below is historical, not the current trigger contract.

Stages 01–08 retain their original offline/VM acceptance milestones. Stage 07
passed 15/15 real VM E2E scenarios; its accepted implementation passed 779 offline
tests. Stage 08's original check passed 785 tests. These counts are historical,
not the size of today's suite. The separate Stage 05 local smoke was not run.
Each stage needs its own scoped task, validation evidence and review. This roadmap
does not authorize production actions, merges, release tags or changes to other
repositories. See [ARCHITECTURE.md](../ARCHITECTURE.md).

The table retains original stage-local results unless an updated production
milestone is stated. Earlier "no VDS"/"blocked" entries describe that task's
acceptance boundary, not a present-day prohibition or current server state.

| Stage | Deliverable | Status / acceptance boundary |
| --- | --- | --- |
| Stage 01 — Project Bootstrap | Local standards, lock, architecture, bilingual documentation, offline checks | Implemented; PR #1 merged |
| Stage 02 — Service Registry & Manifest Validation | Reviewed deployment contract, versioned schema, admission rules and invalid-input fixtures | Implemented offline; PR #2 merged |
| Stage 03 — Desired State & Reconciliation Planning | Desired/actual models, deterministic diff/plan and no-op semantics | Implemented offline; explicit fixtures, no discovery or host mutations; PR #3 merged |
| Stage 04 — Caddy Gateway Foundation | Deterministic route rendering, candidate validation and gateway interface | Implemented offline; deterministic retained route previews and opt-in validation; no runtime activation; PR #4 merged into develop |
| Stage 05 — Docker Deployment Engine | Deterministic Compose, Docker discovery, preflight, scoped executor, locking and idempotency | Implemented foundation with offline mocks; local smoke not-performed; production apply blocked pending full observation integration; PR #5 merged into develop |
| Stage 06 — Health Checks, Rollback & Recovery | Readiness gates, last-known-good journal and bounded restoration | Implemented offline: bounded readiness, SQLite/LKG, constrained rollback, analysis-only crash recovery and composite interfaces; PR #6 merged into develop |
| Stage 07 — Local E2E & Integration Testing | Isolated end-to-end registration/update/failure/recovery scenarios | Implemented and accepted: 15/15 real local VM scenarios passed; 6/6 GHCR digests verified; no production deployment, VPS/DNS/real certificates |
| Stage 08 — GitHub Actions & Automation | Offline PR CI for develop/main, actionlint, retained-route fix and manual evidence-first VM completion | Implemented offline; 785 tests; VM export/stop/deletion unperformed; branch rules manual; production triggers remain gated |
| Stage 09.1 — Production Contracts & Security | Producer audit, inactive template, narrow admission, manual protected-source policy | Implemented offline; no verified production digest or VDS contact |
| Stage 09.2 — Production VDS Read-only Diagnostics | Existing inventory/trust adapter, bounded SSH probes, private report, three Make commands | Implemented/tested offline; no real SSH/VDS inspection or mutations |
| Stage 09.3 — Production Docker & Caddy Integration | Runtime security, Engine read adapter, complete-source composition, OCI/source gates, managed gateway contracts and same-engine rehearsal | Implemented/tested offline: 1055 tests; production writes and unconfigured live transports remain blocked; no VDS deployment |
| Stage 09.4A — Production Deployment Preparation | Stable Compose plugin >= 2 and capability policy, explicit source trust, canonical lock/storage, bootstrap and candidate/evidence interfaces, execution ADR | Implemented and checked offline: 1120 tests; privileged mechanism, authenticated collectors and real-image runtime evidence remain blocked; no production writes |
| Stage 09.4B — First Authorized ProjectRouter Rollout | Review/install actual production wiring and separately approve bootstrap and exact rollout candidate | Complete per operator (2026-10-28); original production/recovery receipts remain private |
| Stage 09.4B.3 — Production Execution & Authorization | Host-local one-shot mechanism, exact candidates, consumed authorization ledger, same engine/lock/lifecycle and installation handoff | Implemented; operator confirms completed installation and first rollout; future operations retain signature/source/state gates |
| Stage 09.4B.4 — Installation & Production Integration | Exact install bundles, independent manager source review, administrative initial installer, local read sources and separately approved GHCR cache preparation in the existing executor | Implemented; initial installation complete per operator; future administrative operations require separate authority |
| Stage 09.4B.7 — Trusted Caddy Image Producer | Authenticated locked release/rootfs, PR build, protected-main GHCR publication, signed provenance and separate Caddy verification | Implemented; signed main publication and isolated runtime acceptance established; gateway deployment complete per operator |
| Stage 09.5 — Update, Rollback & Release Acceptance | Controlled production update/rollback and operational acceptance | Automatic ProjectRouter updates confirmed through 2026-10-30; live rollback acceptance still needs separate evidence |
| Stage 09.6 — Automated Continuous Deployment | Protected producer CI calls pinned shared CD under signed policy; Manager main release triggers Manager CD | Implemented; ProjectRouter automatic updates successful; Manager CD attempt 2 successful with verified installed revision/health and no-op on 2026-10-30 |

Stage 02 defines the [v1 application and admission contracts](manifest-v1.md),
accepts immutable OCI manifest/index digests with explicit `linux/amd64`, and
provides a bounded Python/PyYAML/jsonschema validator with offline pytest fixtures.
The real registry now contains the enabled ProjectRouter manifest with a
verified origin-bound artifact. Stage 09.1
admission reserves ProjectRouter identity/source/image/hostname and runtime contract. Validator tooling does not choose the future controller language.

Stage 03 implements [offline reconciliation planning](reconciliation-planning.md):
deeply immutable models, bounded closed snapshots, component-scoped diffs, route
and health outcomes, lifecycle/storage blockers, CLI and Make integration.
Fixtures are not VPS observations or execution authorization.

Stage 04 implements [offline Caddy gateway foundation](caddy-gateway.md):
immutable routes/candidates, separate desired/current/retained sets, deterministic
Caddyfile, Stage 03 outcome adaptation, CLI and opt-in official local validation.
Offline tests pass; no Caddy binary is installed in the implementation environment,
so actual official adapt/validate did not run. PR #4 is merged into `develop`.

Stage 05 implements the [Docker engine foundation](docker-deployment-engine.md)
as a Python CLI with no daemon. Existing Stage 03 blockers remain intact; Docker
inventory cannot prove full registry/gateway observations. No production apply
interface exists. Runtime refs and OCI indexes without verified mapping block;
images must be preloaded. Local smoke is implemented but not run automatically or
during this implementation. Unit tests require no Docker.

Stage 06 implements [readiness, journal and recovery](readiness-recovery.md) in the
existing executor with SQLite v1, atomic application LKG and conservative stateless
image rollback. Crash recovery is analysis-only; unfinished runs cannot be blindly
resumed. Composite sources have offline contracts/fakes; Stage 07 adds VM-only readers.
PR #6 is merged into `develop`.

Before production execution, decide full fresh observation and real readiness
transports, protected production source enforcement, runtime configuration
delivery, OCI index mapping, durable local storage/retention, backups and recovery
ownership. Stage 07 implements [disposable Ubuntu VM E2E infrastructure](local-e2e.md),
separate prepare/images/run/diagnose/clean opt-ins, independently collected
Docker/OCI/runtime/readiness/Caddy evidence and the existing executor/lifecycle.
Offline tests remain simulated and distinct from the completed real VM acceptance.
See [acceptance evidence and earlier failed attempts](local-e2e.md#verified-local-acceptance).
Local VM acceptance does not verify or authorize production deployment.
The owner selected the [MIT License](../LICENSE); vendored standards retain their
[upstream notice](../.agents/skills/UPSTREAM-LICENSE.txt).

Stage 08 uses `make check` and `make workflow-check`, without a redundant `ci`
alias. [Automation and VM completion](stage-08-automation.md) documents pinned tool
setup, required GitHub checks and separate export/review/stop/delete boundaries.
No existing VM is deleted by implementation or CI. Historical failed sessions and
recovery fences remain unchanged; no new recovery framework or production trigger
is introduced.

Stage 09.1 [production contracts/security](production-contracts.md) audits the
producer at an exact GitHub revision and keeps its untranslated sample out of the
registry. The manager keeps canonical resource names and alias, pins `/en/`
readiness and admits only the confirmed hostname/source/image/platform.
Manual approval from protected `main` is selected; enforcement and exact-SHA
validation must be established before production. The document defines concrete
inputs/acceptance for 09.2 read-only diagnostics, 09.3 production adapters/Caddy
(including missing runtime hardening), 09.4 first rollout and 09.5 update, rollback,
acceptance/release. None is authorized by ordinary merge or successful offline CI.

Stage 09.2 [read-only diagnostics](production-inspection.md) consumes the existing
local infrastructure inventory and host trust, never provisions access. Check and
report commands are offline; inspect requires a terminal and exact confirmation
after separate live-operation authorization. Synthetic transports test failure,
permissions, injection, conflicts, bounded output and private atomic storage.
No real VDS was inspected. Reports are diagnostic snapshots, never complete
planner observations. Stage 09.3 adds the offline contracts/ports below. Stage 09.4 still needs accepted
fresh diagnostics, permissions, reviewed paths/subnets, actual-image compatibility,
configured live transports and artifact/source/DNS/TLS/GHCR prerequisites. Historical local VM E2E evidence is
unchanged and does not assert that previously used VMs still exist.

Stage 09.3 [production integration](production-integration.md) implements reusable
Ports/Adapters contracts and fake lifecycle integration over Stages 03–07, with
versioned runtime security and deterministic Caddy/Compose topology. Saved-input
preflight/plan remain blocked previews. Actual protected-main source verification,
origin/provenance evidence, trusted host/runtime transports and write authorization
remain explicit Stage 09.4 gates. No server inspection or deployment was performed.

Stage 09.6 now has a concrete minimal CD implementation in `scripts/cd/`,
`installation/` and the production workflows. ProjectRouter invokes a pinned
shared workflow after successful main-push CI; Manager delivery follows its
successful trusted release. The former proposal to create digest-update PRs is
not the current automatic application path. See [current delivery flow](production-status.md#what-happens-after-a-projectrouter-update).

Stage 09.4 is now explicitly split into [09.4A preparation](production-preparation.md)
and **09.4B — First Authorized ProjectRouter Rollout**. Preparation reuses the
existing executor/lifecycle and introduces no production apply or automatic CD.
The [execution ADR](adr/0001-production-execution.md) defines the infrastructure
handoff. Operator-reported 2026-10-22 diagnostics are not fresh trusted planner
state. Those preparation-time source/branch observations are historical; they are not
current GitHub Settings or producer code observations.
Stages 09.5 and 09.6 retain their independent acceptance/authorization boundaries.

## Universal application acceptance

The specialized ProjectRouter acceptance from PR #16 is replaced with one manual
ID-selected workflow and a shared runner, using manifest `release` metadata and
independent admission `acceptance` constraints. HTTP/worker, multiple components,
readiness-gated dependencies, restart, runtime security and explicitly disposable
test volumes are covered offline. See [how to add applications](application-acceptance.md).
ProjectRouter is now enabled in the registry with its signed build-origin
contract. Universal isolated acceptance passed in run 39837965499;
Stage 09.4B production admission/authorization and later stages remain separate.

## Stage 09.4B.1 — Caddy runtime acceptance

[Dedicated acceptance](caddy-runtime-acceptance.md) pins official Caddy 2.11.7 for
linux/amd64, reuses existing production contracts and adds isolated GitHub-hosted
runtime scenarios. All 17 real cases and cleanup passed in run 39844325743.
Offline checks, disposable Docker evidence and unperformed production rollout
are tracked separately. ProjectRouter isolated acceptance run
39837965499 and main Offline CI run 39839861288 are confirmed successful, superseding
earlier pending-run notes. Caddy build-origin signature verification remains a
production blocker; Stage 09.4B rollout and Stages 09.5–09.6 remain separately scoped.

## Stage 09.4B.5 — Production trust readiness

[Readiness handoff](production-trust-readiness.md) prepared solo-maintainer source
policy B. Stage 09.4B.6 activates the maintainer-approved B in the production release
collector and independent bundle reviewer using one fixed trusted-code constant;
low-level A remains available. Fresh read-only API checks confirm strict App-bound
CI, stale-review dismissal, PRs and zero required approvals in both Rulesets.
No Settings write occurred. Exact-digest official
Caddy registry probes did not establish authenticated publisher/builder provenance;
recommended signed rebuild and upstream-evidence alternative require operator choice.
The handoff orders release, independent bundle review, separately permitted read-only
VDS diagnostics, initial install, image preparation, Caddy bootstrap and first rollout.
Bindings/real-host evidence remain missing; no production authorization or run follows.
