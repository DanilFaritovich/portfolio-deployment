# ProjectRouter v1.0.0 isolated acceptance

Status: candidate and manual workflow prepared; **real-image acceptance has not
been run**. This work does not admit a production release or authorize deployment.

## Candidate and evidence

The [inactive candidate](candidates/project-router.yaml) preserves the runtime contract of the existing
[template](templates/project-router.yaml), with exactly:

- Image: `ghcr.io/danilfaritovich/project-router@sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853`
- Producer: `DanilFaritovich/project-router`
- Source SHA: `526c765f33932e163392dd1d512de4aa2f2a5260`
- Publish run: [39776962924, attempt 1](https://github.com/DanilFaritovich/project-router/actions/runs/39776962924/attempts/1)
- Build origin: `https://danil-miftakhov.dev`; platform: `linux/amd64`.

The owner reports protected-main integration, successful exact-SHA CI/publication,
independent `gh attestation verify` success for SLSA and custom origin attestations,
and matching saved OCI evidence hashes. These are owner-reported results, not a
new manager verification or a complete release admission decision.

The candidate remains outside `registry/applications/`, with `enabled: false`.
The admission proposal now also constrains acceptance signer workflows/refs,
required predicates and the exact runtime security contract; it needs security review. Every candidate validation uses the normal full
validator and policy in a temporary registry; this is an offline contract check.
Web HTTP 8080 `/en/`, UID/GID 101:101, read-only root, ALL capabilities dropped,
no-new-privileges, 16 MiB `/tmp`, preserved image healthcheck, stateless storage,
no runtime secrets/volumes/host ports and the single hostname are unchanged.

## Universal isolated acceptance

ProjectRouter is the first declaration for the [universal acceptance runner](application-acceptance.md).
Its one YAML now includes `release.apiVersion: portfolio.acceptance/v1` and per-component
source/signer SHA, workflow/ref, SLSA and exact build-origin predicate expectations.
Independent admission requires both predicates and pins the reviewed runtime contract.
The specialized runner/workflow have been replaced by
[application-acceptance.yml](../.github/workflows/application-acceptance.yml) and
`scripts/application_acceptance.py`. No application-name branches exist in Python.

After review/integration on a protected branch, the owner can explicitly dispatch
**Application isolated acceptance**, choosing `application=project-router` and
`confirmation=isolated-acceptance`. This task does not execute it. See the universal
guide for prerequisites, schema, resource cleanup, receipt and verification boundaries.

Acceptance verifies the signed origin claim, OCI/Engine identity, `/en/` readiness,
image HEALTHCHECK when present, runtime security and restart readiness. PDF/viewer,
SEO/sitemap, UI/assets/locales and API business behavior belong to producer CI and
are no longer duplicated in manager acceptance. Exact digest, source SHA, origin,
security, `enabled: false` and location outside the active registry are preserved.

## Remaining admission blockers

Until an owner triggers and reviews this workflow, independent live byte mapping,
real-image runtime/security/restart/HTTP acceptance and its report remain pending.
A successful run is only isolated image acceptance. The manager's existing
production `ReleaseVerifier`, authenticated collector/attestors and exact
OperationCandidate approval gate remain required and are not wired to consume
this report. Both repositories' effective protected-main rules/ancestry,
exact-SHA required CI/check identities, publish identity and complete retained
provenance/origin evidence still require independent admission review.

Admission must bind the reviewed manifest/policy, protected manager revision,
artifact and source evidence to an approved operation. Host/daemon trust,
production access/storage/network ownership, infrastructure handoff, DNS/TLS,
first-install containment and later rollback remain separate production gates.
No candidate registration, activation, production write transport, automatic CD,
merge, tag or release is authorized or performed here. See
[production contracts](production-contracts.md),
[integration](production-integration.md) and
[preparation](production-preparation.md).
