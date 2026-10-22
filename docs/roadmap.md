# Preliminary implementation roadmap

This sequence may change after architecture review. Stages 01–04 are implemented
offline; Stage 05 adds a constrained engine foundation and opt-in local smoke.
Stage 06 adds offline readiness, SQLite journal/LKG and constrained recovery.
Stage 07 passed **15/15 real local VM E2E scenarios** in session `4963fe050c4567f2`
on 2026-10-21; all six GHCR fixture digests were published and anonymously verified.
The Stage 07 implementation passed **779 offline tests**. Stage 08 adds offline
PR CI, the Caddy retained-route regression fix and a manual successful-session VM
completion procedure; its final offline check passed **785 tests**. Stage 09.1 adds offline production contracts/security; Stage 09.2 adds an offline-tested read-only diagnostics interface; Stages 09.3–09.5 and
production execution remain **Planned**; the separate Stage 05 smoke was not run.
Each stage needs its own scoped task, validation evidence and review. This roadmap
does not authorize production actions, merges, release tags or changes to other
repositories. See [ARCHITECTURE.md](../ARCHITECTURE.md).

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
| Stage 09.3–09.5 — Production Integration & Release | Adapters/Caddy, first rollout, update/rollback and release | Planned; accepted diagnostics, stage-specific prerequisites and explicit permissions required |

Stage 02 defines the [v1 application and admission contracts](manifest-v1.md),
accepts immutable OCI manifest/index digests with explicit `linux/amd64`, and
provides a bounded Python/PyYAML/jsonschema validator with offline pytest fixtures.
The real registry is empty pending a confirmed origin-bound artifact. Stage 09.1
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
No project license has been chosen;
resolve it before project release.

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
planner observations. Stage 09.3 still needs accepted fresh diagnostics, permissions,
reviewed paths/subnets, runtime hardening, complete adapters/Caddy and the existing
artifact/source/DNS/TLS/GHCR prerequisites. Historical local VM E2E evidence is
unchanged and does not assert that previously used VMs still exist.
