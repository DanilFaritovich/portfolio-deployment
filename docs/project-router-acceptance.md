# ProjectRouter v1.0.0 isolated acceptance

Status: candidate and manual workflow prepared; **real-image acceptance has not
been run**. This work does not admit a production release or authorize deployment.

## Candidate and evidence

The [inactive candidate](candidates/project-router.yaml) substitutes the existing
[template](templates/project-router.yaml) with exactly:

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
Admission policy is unchanged. Every candidate validation uses the normal full
validator and policy in a temporary registry; this is an offline contract check.
Web HTTP 8080 `/en/`, UID/GID 101:101, read-only root, ALL capabilities dropped,
no-new-privileges, 16 MiB `/tmp`, preserved image healthcheck, stateless storage,
no runtime secrets/volumes/host ports and the single hostname are unchanged.

## Reviewed manual trigger

The [workflow](../.github/workflows/project-router-acceptance.yml) has only
`workflow_dispatch`. It never runs on PR, push, schedule, workflow completion or
producer publication. Do not execute it during implementation. Review and merge
this PR into protected `develop` first. GitHub requires the workflow to exist on the default branch before dispatch;
see [manual workflow execution](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow). If the default
branch is `main`, integrate the reviewed workflow there through the repository's
separate protected-branch process; this task does not authorize that merge.
Both workflow condition and Python gate require a protected `develop` or `main`.

After review and appropriate integration, an owner can explicitly authorize the
disposable runner operation through Actions → **ProjectRouter isolated acceptance**
→ Run workflow, selecting the reviewed protected branch and entering
`project-router-v1.0.0-isolated`. The CLI equivalent is:

```sh
gh workflow run project-router-acceptance.yml \
  --repo DanilFaritovich/portfolio-deployment --ref develop \
  -f confirmation=project-router-v1.0.0-isolated
```

The workflow checks out that manager revision, which is recorded in evidence.
It accepts no image/source/origin/command override. Permissions are `contents: read`
only. The ephemeral GitHub-hosted Ubuntu 24.04 x64 runner supplies Docker, Compose
and GitHub CLI; missing tools or unsupported verification flags fail closed.
Pinned Python and development dependencies provide the existing offline validator.
No production Environment, secrets, SSH, VDS, Multipass, Caddy or Docker socket
inside the application is involved. The workflow uses its read-only GitHub token
only in the host-side GitHub CLI verification process. Registry reads/pull are
anonymous; private GHCR access is deliberately unsupported.

## Acceptance and evidence scope

The runner reads the immutable GHCR index/manifest/config bytes independently and
hashes their original bytes. The shared manager OCI verifier checks digest, media
type, descriptor size, unique `linux/amd64` child and config mapping. A pull uses
the exact immutable reference, without rebuilding or checking out producer code.
The existing Engine binding compares config ID, RepoDigests, architecture and
image defaults. Unsupported Engine identity representation blocks acceptance;
there is no image-ID fallback or relaxation for containerd stores.

For both predicates, `gh attestation verify` authenticates signatures and subject,
repository, signer `.github/workflows/publish.yml`, signer/source SHA, source ref
`refs/heads/main` and GitHub-hosted signing runner. Verification uses GitHub API
attestations and default trust roots. Its successful JSON output is then checked
for the exact subject and predicate type. The custom predicate must match the
exact origin build argument, source, platform, output-verification claim and
publish run/attempt. The flag/output contract follows the
[GitHub CLI verification manual](https://cli.github.com/manual/gh_attestation_verify).
Missing, duplicate, mismatched or unverifiable attestations stop before runtime.
This accepts the authenticated producer's build-origin claim; it does not make
producer-controlled predicate fields independent manager source-policy evidence.

The normal Compose renderer projects the still-disabled declaration in its
existing test-session mode. All resource names and the ingress alias are uniquely
prefixed `pd-smoke-<random-session>` / `<session>-project-router-web`. The test-only
proxy bridge is internal and disposable; it is not `portfolio-proxy`. No route or
public port is created. HTTP probes originate on the runner against the private
container IP, with the real Host header. Runtime execution is ordinary bounded
Compose test orchestration, not a new deployment engine or production adapter.

Acceptance checks startup/image healthcheck; effective Docker settings and
PID 1 UID/GID, capability sets and no-new-privileges; actual bounded tmpfs mount;
no published application ports; `/en/`, `/ru/`, root 302, unknown route 404,
generated JS/CSS, a public PNG, representative EN/RU PDFs with MIME/magic bytes,
locale/origin-bearing HTML and sitemap/robots. Restart must produce a new start
time and recover health and localized HTTP. The root filesystem must remain
unchanged after requests and restart. It does not repeat producer unit/browser
suites or claim complete SEO/PDF content verification.

Commands, downloads, response sizes and health waits are bounded, with a 15-minute
job timeout. The artifact retains only `acceptance-evidence/report.json` for 30
days: fixed stage/result, exact identities, OCI digests/sizes, verified-output
hashes, Compose hash, per-path response hashes/sizes/status, runtime and cleanup
results. It omits raw environment/config/inspect, bearer tokens, exceptions,
container logs and HTTP bodies. It is a sanitized acceptance receipt, not a
portable signature bundle or a production trust object. Independently retain or
recollect original OCI/signature evidence for release review. Failure reports
show the failed stage; no failure becomes a passing admission flag.

Cleanup runs in `finally` and removes only exact session/project-owned containers
and empty networks whose inspected labels/names match the projection. It never
uses `down`, volume removal, image removal or prune. Hard runner termination may
interrupt cleanup; disposal of the GitHub-hosted runner provides the final
containment boundary. Such a run is not accepted as success.

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
