# Preliminary implementation roadmap

This sequence may change after architecture review. Stages 01–04 are implemented
offline; Stage 05 adds a constrained engine foundation and opt-in local smoke.
Stage 06 adds offline readiness, SQLite journal/LKG and constrained recovery.
Stages 07–09 and production execution are **Planned**; no live smoke was performed.
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
| Stage 06 — Health Checks, Rollback & Recovery | Readiness gates, last-known-good journal and bounded restoration | Implemented offline: bounded readiness, SQLite/LKG, constrained rollback, analysis-only crash recovery and composite interfaces; real integration pending |
| Stage 07 — Local E2E & Integration Testing | Isolated end-to-end registration/update/failure/recovery scenarios | Planned; no VPS/DNS/real certificates |
| Stage 08 — GitHub Actions & Automation | Deterministic offline PR CI, exact-revision gates and reviewed trigger policy | Planned; production automation remains gated |
| Stage 09 — Production Integration & Release | Separately authorized host integration, DNS/TLS, controlled rollout and release process | Planned; operator acceptance and explicit permissions required |

Stage 02 defines the [v1 application and admission contracts](manifest-v1.md),
accepts immutable OCI manifest/index digests with explicit `linux/amd64`, and
provides a bounded Python/PyYAML/jsonschema validator with offline pytest fixtures.
The real registry/policy are empty pending reviewed applications and confirmed
artifacts. Validator tooling does not choose the future controller language.

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
resumed. Composite sources have offline contracts/fakes, without real readers.

Before production execution, decide full fresh observation and real readiness
transports, protected production source/release policy, runtime configuration
delivery, OCI index mapping, durable local storage/retention, backups and recovery
ownership. Stage 07 expands isolated E2E. No project license has been chosen;
resolve it before project release.
