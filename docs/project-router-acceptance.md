# ProjectRouter v1.0.0 isolated acceptance

Status: Stage C2 independently verified the producer source, publication, OCI bytes
and signed attestations. The production manifest is enabled desired state; manager
protected-main eligibility, installation and rollout authorization remain blocked.
Historical disposable runtime acceptance is recorded separately below.

## Candidate and evidence

The [production manifest](../registry/applications/project-router.yaml) preserves the runtime contract of the existing
[template](templates/project-router.yaml), with exactly:

- Image: `ghcr.io/danilfaritovich/project-router@sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853`
- Producer: `DanilFaritovich/project-router`
- Source SHA: `526c765f33932e163392dd1d512de4aa2f2a5260`
- Publish run: [39776962924, attempt 1](https://github.com/DanilFaritovich/project-router/actions/runs/39776962924/attempts/1)
- Build origin: `https://danil-miftakhov.dev`; platform: `linux/amd64`.

## Stage C2 independent producer verification

Read-only verification on 2026-10-23 UTC used the existing `ProtectedSource`
with approved policy B, `GHCRRead`, OCI byte mapper and `AttestationVerifier`.
No Docker, VDS contact, publication or production activation occurred.

- Exact current protected main: `526c765f33932e163392dd1d512de4aa2f2a5260`.
  Effective enforcement, strict required checks, empty bypass and source stability
  passed the existing verifier before/after collection.
- GitHub Actions App **15368**, main-push run **39776962924**, attempt **1**:
  `Quality and static build` job **119818239159**, `Docker build and container smoke`
  job **119819149287**, `Publish validated image / publish` job **119819781254**;
  all completed successfully. The attempt-specific publication job and exact-SHA
  reusable workflow reference were independently correlated with check runs.
- Release artifact **12424503814**, ZIP SHA256
  `c2c85fee5362d5e7a719ff4f67dad1056ac87cc6373a267a32933964f04eb22e`,
  supplied a locator, not trust. Fresh anonymous GHCR index/manifest/config reads
  independently established the image above, `linux/amd64` manifest
  `sha256:e24e8a7d68f341199ae90ffba8e15d225c9ce38b7a45ebd547ec918136ce72da`
  and image config ID
  `sha256:115af1631e16774d114aa857130086e0a32711720895195c52d0b30ce68df102`.
- Both embedded SLSA v1 and build-origin bundles passed real local Sigstore
  verification, including issuer `https://token.actions.githubusercontent.com`,
  signer workflow `.github/workflows/publish.yml`, source/signer SHA above,
  `refs/heads/main` and denial of self-hosted runners. The exact signed SLSA
  builder is `https://github.com/DanilFaritovich/project-router/.github/workflows/publish.yml@refs/heads/main`;
  the immutable signer SHA is checked separately. Its invocation is the exact
  publication run/attempt above, and resolved source dependency matches that SHA.
- The signed build-origin predicate exactly matches the manifest, including
  `VITE_SITE_ORIGIN=https://danil-miftakhov.dev`, `originOutputVerified=true`,
  platform, source and publication run/attempt. OCI image defaults independently
  match UID/GID `101:101`, TCP `8080` and the `/en/` HEALTHCHECK.
- Verification provisioned gh **2.102.0** using the existing independently pinned
  official archive SHA256 `bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386`;
  executable SHA256 `7469124f706944133d6a169691dd1c6c3511b12e85878d255e044e2948df4c9b`.
  The existing reviewed GitHub Sigstore root snapshot SHA256 is
  `65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c`.
  System gh 2.101.0 was not used for cryptographic verification.

The disabled candidate was promoted rather than duplicated: acceptance rejects
an identity present in both candidate and registry locations. Schema/admission,
release expectations and generated Compose remain offline checks, never source
attestors. `enabled: true` declares the intended first rollout; it supplies no
execution grant. The admission/security policy and both blocking installation
bindings are unchanged. Full `ReleaseVerifier` still requires a future protected
manager-main commit containing these exact manifest/policy bytes, fresh source
checks and actual host-bound operation evidence. This report cannot replace it.

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
`confirmation=isolated-acceptance`. The confirmed execution result is recorded below. See the universal
guide for prerequisites, schema, resource cleanup, receipt and verification boundaries.

Acceptance verifies the signed origin claim, OCI/Engine identity, `/en/` readiness,
image HEALTHCHECK when present, runtime security and restart readiness. PDF/viewer,
SEO/sitemap, UI/assets/locales and API business behavior belong to producer CI and
are no longer duplicated in manager acceptance. Exact digest, source SHA, origin,
security and digest remain unchanged; Stage C2 promoted the declaration into the registry.

## Remaining admission blockers

The isolated workflow now passed, as recorded below: live byte mapping and
real-image runtime/security/restart/HTTP checks have disposable-run evidence.
This is only isolated image acceptance. The manager's existing
production `ReleaseVerifier`, authenticated collector/attestors and exact
OperationCandidate approval gate remain required and are not wired to consume
this report. Stage C2 verified the producer evidence above. The manager protected-main revision
and both repositories' fresh source/release evidence remain required at execution.

Admission must bind the reviewed manifest/policy, protected manager revision,
artifact and source evidence to an approved operation. Host/daemon trust,
production access/storage/network ownership, infrastructure handoff, DNS/TLS,
first-install containment and later rollback remain separate production gates.
Stage C2 adds desired registration only. No activation, production write transport,
automatic CD, merge, tag or release was performed. See
[production contracts](production-contracts.md),
[integration](production-integration.md) and
[preparation](production-preparation.md).


## Confirmed isolated acceptance

GitHub API confirms [Application isolated acceptance 39837965499](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39837965499)
completed successfully at manager SHA `6b4173d7c0d51439041a59bd12cc6a6a4d3b161d`.
[Main Offline CI 39839861288](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39839861288)
passed at `65af84bd0de23af9cf9ae40b2c0c411e1fbe2c36`. Shared real-Docker runtime
integration was also reported passed; these checks are separate from offline CI.
No production operation or candidate activation follows from those results. The
ProjectRouter digest/admission were unchanged by that historical acceptance;
Stage C2 subsequently promoted the manifest as described above.

The separate [Caddy runtime acceptance](caddy-runtime-acceptance.md) closes only
its own disposable image/runtime evidence gate. Production source/release verification,
trusted host/gateway observations, Caddy provenance and exact operation approval
remain prerequisites for the separately authorized Stage 09.4B rollout.
