# Preliminary implementation roadmap

This sequence may change after architecture review. Only Stage 01 is implemented
in this branch; Stages 02–09 are **Planned** and have no runtime implementation.
Each stage needs its own scoped task, validation evidence and review. This roadmap
does not authorize production actions, merges, release tags or changes to other
repositories. See [ARCHITECTURE.md](../ARCHITECTURE.md).

| Stage | Deliverable | Status / acceptance boundary |
| --- | --- | --- |
| Stage 01 — Project Bootstrap | Local standards, lock, architecture, bilingual documentation, offline checks | Implemented in this branch; PR review pending |
| Stage 02 — Service Registry & Manifest Validation | Reviewed deployment contract, versioned schema, admission rules and invalid-input fixtures | Planned; offline validation only |
| Stage 03 — Desired State & Reconciliation Planning | Desired/actual models, deterministic diff/plan and no-op semantics | Planned; no host mutations |
| Stage 04 — Caddy Gateway Foundation | Deterministic route rendering, candidate validation and gateway interface | Planned; offline/local foundation, no real domains |
| Stage 05 — Docker Deployment Engine | Scoped Compose execution, digest pulls, serialization and idempotency | Planned; disposable local environment first |
| Stage 06 — Health Checks, Rollback & Recovery | Readiness gates, last-known-good journal and bounded restoration | Planned; data/migration limits explicit |
| Stage 07 — Local E2E & Integration Testing | Isolated end-to-end registration/update/failure/recovery scenarios | Planned; no VPS/DNS/real certificates |
| Stage 08 — GitHub Actions & Automation | Deterministic offline PR CI, exact-revision gates and reviewed trigger policy | Planned; production automation remains gated |
| Stage 09 — Production Integration & Release | Separately authorized host integration, DNS/TLS, controlled rollout and release process | Planned; operator acceptance and explicit permissions required |

Before Stage 02 decide schema format/versioning, service/domain ownership and
admission policy, allowed GHCR producers, OCI index versus platform digest policy,
runtime contract fields and fixture acceptance criteria. Choose validator tooling
with rationale, without implicitly selecting the future controller language.

Before execution stages decide controller CLI/CI model and language, production
source/release policy, secret delivery, journal storage, concurrency strategy,
network isolation, update/downtime policy, migration compatibility and recovery
ownership. No project license has been chosen; resolve it before project release.
