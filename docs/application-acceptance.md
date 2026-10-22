# Как добавить новое приложение в Portfolio Deployment

Application CI owns business logic, PDF/viewer, SEO/sitemap, frontend UI and API
functional tests. It builds and publishes immutable public GHCR images. The
Deployment Manager verifies artifact identity and the deployment contract.

## Minimal process

1. Publish a `linux/amd64` GHCR image through the application's trusted CI. Retain
   its immutable digest, source SHA, signer SHA, workflow, source ref and signed
   SLSA provenance. A worker needs a meaningful image HEALTHCHECK. HTTP components
   need a readiness endpoint returning HTTP 200 without redirect; an image
   HEALTHCHECK, when present, must also become healthy.
2. Add **one Application YAML**, initially `docs/candidates/<id>.yaml`, with
   `enabled: false`. Include the existing manifest v1 runtime contract and optional
   versioned `release` metadata shown below. No new runner, workflow or engine
   code is needed for a supported application. Do not add producer Compose files.
3. Obtain separate admission/security review of `policy/admission.yaml`: source
   repository, per-component image repositories, platform, hostname and
   `acceptance` constraints. Release metadata supplies expected values, never
   evidence, trust policy or execution authorization. Policy changes in this PR
   are review proposals until reviewed/merged. Keep policy review independent of
   producer-provided declarations. Protect policy changes with required review.
4. Run `make fix`, `make acceptance-check`, `make check` and `make workflow-check`
   with pinned development tools. Ordinary manager CI validates inactive candidates
   as well as the active registry, offline. Active entries can also be selected by
   ID; duplicate candidate/active identities are rejected. Candidates are validated
   together with the real registry for global collisions.
5. After review/integration, explicitly run **Application isolated acceptance** on
   protected `develop` or `main`: input `application=<id>` and
   `confirmation=isolated-acceptance`. There is one dispatch-only workflow for all
   applications. GitHub requires it on the default branch before dispatch is
   available. This document does not authorize running acceptance during development.
6. Review the sanitized report. Production still requires the full independent
   release/source verifier, both repositories' protected-main/required-CI evidence,
   infrastructure/operation gates and explicit authorization. Move a reviewed
   candidate into `registry/applications/` only through that separate process.
   Acceptance never sets `enabled`, modifies the registry or grants production access.

## Static site example

The real, still-disabled [ProjectRouter candidate](candidates/project-router.yaml)
illustrates the complete static site declaration, including its exact v1.0.0
image, source SHA, signed build-origin expectation and runtime security.
[Manifest v1](manifest-v1.md) documents the existing fields. A minimal shape is:

```yaml
apiVersion: portfolio.deployment/v1
kind: Application
metadata:
  id: example-site
  sourceRepository: Example/site
spec:
  enabled: false
  components:
    - name: web
      image: ghcr.io/example/site@sha256:<64-lowercase-hex>
      platform: linux/amd64
      type: http
      port: 8080
      health: {strategy: http, path: /ready}
      runtimeSecurity:
        apiVersion: portfolio.runtime/v1
        readOnlyRootFilesystem: true
        user: '1000:1000'
        capDrop: [ALL]
        noNewPrivileges: true
        restart: unless-stopped
        imageHealthcheck: preserve
        tmpfs: []
  storage: {type: stateless}
  ingress: {hostname: site.example.org, component: web, port: 8080}
release:
  apiVersion: portfolio.acceptance/v1
  components:
    web:
      sourceSha: <40-lowercase-hex>
      signerSha: <40-lowercase-hex>
      signerWorkflow: .github/workflows/publish.yml
      sourceRef: refs/heads/main
      attestations:
        - predicateType: https://slsa.dev/provenance/v1
```

Placeholders must be replaced with verified release identities. They are not valid
schema values. Policy must independently allow the source/image/hostname and add:

```yaml
acceptance:
  signerWorkflows: [.github/workflows/publish.yml]
  sourceRefs: [refs/heads/main]
  requiredPredicates: [https://slsa.dev/provenance/v1]
  runtimeSecurity:
    web: # exact reviewed runtimeSecurity object from the Application
      apiVersion: portfolio.runtime/v1
      readOnlyRootFilesystem: true
      user: '1000:1000'
      capDrop: [ALL]
      noNewPrivileges: true
      restart: unless-stopped
      imageHealthcheck: preserve
      tmpfs: []
```

For a build-time origin contract, admission `staticSite` binds the origin,
component, port and readiness path. Independently declare the origin attestation
in policy `requiredPredicates` and `predicateClaims`, for example:

```yaml
predicateClaims:
  - predicateType: https://github.com/Example/site/attestations/build-origin/v1
    expectedPredicate:
      buildArguments: {VITE_SITE_ORIGIN: 'https://site.example.org'}
      originOutputVerified: true
```

These constraints pin required top-level claims in the declaration's
`expectedPredicate`. That declaration supplies the complete expected predicate,
including release-specific source/run fields. Successful signature verification
must produce an exact JSON match, preserving JSON types. The runner never
interprets application-specific predicate keys. ProjectRouter's policy independently
requires its build origin, source repository/ref, platform and output-verification
claim. The attestation command binds exact source/signer SHAs. Such signed claims
still do not replace independent production source review.

## Backend + worker example

For a backend, use the same YAML shape with `metadata.id: example-backend`, its
reviewed source/image repositories and an HTTP `api` component with `/ready`.
Add a worker to `spec.components`:

```yaml
- name: worker
  image: ghcr.io/example/worker@sha256:<64-lowercase-hex>
  platform: linux/amd64
  type: worker
  health: {strategy: imageHealthcheck}
  dependsOn: [api]
  runtimeSecurity:
    apiVersion: portfolio.runtime/v1
    readOnlyRootFilesystem: true
    user: '1000:1000'
    capDrop: [ALL]
    noNewPrivileges: true
    restart: unless-stopped
    imageHealthcheck: preserve
    tmpfs: []
```

Declare both `release.components.api` and `release.components.worker` with their
own exact source SHA, signer SHA, signer workflow/ref and attestations. Both images
must come from the application's independently admitted source repository; separate
producer repositories require separate Applications. Add both image repositories
and exact runtime contracts to admission. No Python or workflow changes are needed.
Startup follows `dependsOn`, waiting for each dependency's readiness. During initial
verification and after restart, the runner checks Docker DNS from the dependent's
network namespace, the expected private IP and TCP reachability of HTTP dependencies.
Worker dependencies without a listening port use DNS plus their declared image
healthcheck; no fabricated TCP service or business request is introduced.

For persistent storage, use the normal manifest v1 `storage.volumes` and
`volumeMounts`, plus `release.volumePolicy: remove-isolated`. This explicitly
consents to deleting only newly created test volumes. All volume names use the
renderer-generated random test-session prefix; there are no external/bind mounts,
production names, restore operations or existing volume reuse. Production backup
policy remains separate. Non-root ownership initialization must already work with
the image and an empty `nocopy` volume; acceptance cannot run setup shell hooks.

## Verification and resource boundaries

The runner validates the complete real registry/policy and selected declaration
before downloads or Docker access. Optional `release` and policy `acceptance`
fields extend existing v1 schemas without changing old manifest/engine semantics.
They use `portfolio.acceptance/v1`; unknown fields and unsupported values fail.
Each component needs exactly one release identity and independently reviewed runtime
contract. Metadata cannot add Docker options, commands, build hooks or environment.
`expectedPredicate` is bounded JSON data for exact equality, never executed code.
Missing acceptance policy/release, secrets/config references, anonymous image
volumes and absent worker HEALTHCHECK produce `blocked`, never a passing report.
Malformed/unadmitted declarations or failed verification produce `failed`.

Before **any** image pull, every component passes anonymous GHCR byte/digest checks,
shared OCI index/manifest/config verification (including unique `linux/amd64`
mapping), and signed attestation verification. The runner downloads GitHub CLI
2.102.0 from its pinned release, checks the compiled-in archive SHA-256 **before
parsing or executing it**, and checks its exact version (minimum 2.102.0). There is
no PATH fallback. Verification binds repository, subject digest, signer workflow,
signer SHA, source SHA/ref and rejects self-hosted signing runners. Then the shared
Engine binding verifies pulled config ID, RepoDigests, platform and image defaults.

The existing Compose renderer creates isolated names, labels, private internal
networks and optional internal test proxy network. No production adapter/lifecycle,
Caddy activation or application host port is used. The common readiness gate
requires two consecutive successes, pins container identity and bounds probes.
Shared runtime inspection checks actual image/config, networks/aliases, mounts,
security and absence of host ports. Host-side `/proc` inspection checks PID 1
UID/GID, capabilities, no-new-privileges and actual tmpfs flags/size; `docker diff`
checks the read-only image root. Restart must change every start timestamp and
restore readiness, dependencies and security. The fixed network probe uses host
Python with `sudo nsenter --net`; no arbitrary YAML command or application tooling
is executed. No Docker socket or host binaries are mounted inside the application.

Before mutation, preflight rejects collisions with any planned network, volume or
Compose project. Cleanup runs after partial startup failures too. Exact inspected
session/project/application/managed labels and expected resource names gate removal.
Only owned containers, empty networks and disposable test volumes are removed.
There is no `down`, prune, image deletion or forced volume deletion. Cleanup failure
prevents success. Abrupt cancellation may prevent cleanup; ephemeral runner disposal
is the final containment boundary and cannot generate a positive report.

`acceptance-evidence/report.json` is bounded to 1 MiB and contains fixed stages,
identities, manager revision, Compose hash, OCI mapping/sizes, verified-output hashes
and runtime/cleanup outcome. It excludes raw predicates, responses, commands,
environment, inspect output, logs, credentials and exception text.
`productionAdmission` is always false; no production verifier consumes the receipt.

## Unsupported scenarios and prerequisites

- Private GHCR authentication, runtime secrets/config, migrations/setup hooks,
  external services, custom networks/host mounts/host ports, privileged/root or
  writable-root workloads, image anonymous volumes and different source repositories
  within one Application have no acceptance implementation. Declare supported refs
  where applicable and expect `blocked`; do not omit real requirements to pass.
  Unknown configuration fields are rejected, rather than silently ignored.
- Public image defaults must be suitable for isolated execution with no injected
  environment/config and an internal network. A declaration cannot prove absence
  of undeclared application dependencies; producers must declare requirements and
  retain meaningful healthchecks in their own CI. Acceptance does not test business logic.
- Only Linux amd64 on ephemeral GitHub-hosted Ubuntu 24.04 with rootful local Docker,
  Compose, passwordless sudo/nsenter and readable process evidence is supported.
  Missing tools or incompatible Engine evidence fail closed. HTTP only, no TLS,
  redirects or custom auth/header probes. No production DNS/TLS is tested.
- Admission/security review and protected branch settings are mandatory trusted
  configuration outside the one application YAML. No production secrets are needed.
  The workflow accepts only an ID and explicit isolated confirmation, has
  `contents: read`, and uses its read-only token only for host-side attestation
  verification. No production Environment, SSH or VDS access exists.

The implementation is covered by offline mocks, including a second HTTP + worker
application, storage and failure boundaries. Real universal acceptance has **not**
been executed by this task. See [ProjectRouter release handoff](project-router-acceptance.md).
