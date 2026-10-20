# Preliminary implementation roadmap

This sequence may change after architecture review. Stages 01–02 are implemented
offline in this branch; Stages 03–09 are **Planned**. No runtime is implemented.
Each stage needs its own scoped task, validation evidence and review. This roadmap
does not authorize production actions, merges, release tags or changes to other
repositories. See [ARCHITECTURE.md](../ARCHITECTURE.md).

| Stage | Deliverable | Status / acceptance boundary |
| --- | --- | --- |
| Stage 01 — Project Bootstrap | Local standards, lock, architecture, bilingual documentation, offline checks | Implemented in this branch; PR review pending |
| Stage 02 — Service Registry & Manifest Validation | Reviewed deployment contract, versioned schema, admission rules and invalid-input fixtures | Implemented offline; review pending |
| Stage 03 — Desired State & Reconciliation Planning | Desired/actual models, deterministic diff/plan and no-op semantics | Planned; no host mutations |
| Stage 04 — Caddy Gateway Foundation | Deterministic route rendering, candidate validation and gateway interface | Planned; offline/local foundation, no real domains |
| Stage 05 — Docker Deployment Engine | Scoped Compose execution, digest pulls, serialization and idempotency | Planned; disposable local environment first |
| Stage 06 — Health Checks, Rollback & Recovery | Readiness gates, last-known-good journal and bounded restoration | Planned; data/migration limits explicit |
| Stage 07 — Local E2E & Integration Testing | Isolated end-to-end registration/update/failure/recovery scenarios | Planned; no VPS/DNS/real certificates |
| Stage 08 — GitHub Actions & Automation | Deterministic offline PR CI, exact-revision gates and reviewed trigger policy | Planned; production automation remains gated |
| Stage 09 — Production Integration & Release | Separately authorized host integration, DNS/TLS, controlled rollout and release process | Planned; operator acceptance and explicit permissions required |

Stage 02 defines the [v1 application and admission contracts](manifest-v1.md),
accepts immutable OCI manifest/index digests with explicit `linux/amd64`, and
provides a bounded Python/PyYAML/jsonschema validator with offline pytest fixtures.
The real registry/policy are empty pending reviewed applications and confirmed
artifacts. Validator tooling does not choose the future controller language.

Before execution stages decide controller CLI/CI model and language, production
source/release policy, secret delivery, journal storage, concurrency strategy,
network isolation, update/downtime policy, migration compatibility and recovery
ownership. No project license has been chosen; resolve it before project release.
