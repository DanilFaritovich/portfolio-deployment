# Stage 09.4B.2 — Production release and source trust

Status: read-only implementation and controlled offline integration tests, with
separate diagnostic GitHub/GHCR preflight recorded below. Full live release
verification has not run. Production
execution, approval issuance, deployment and automatic CD remain unavailable.
ProjectRouter and Caddy selected digests, registry and admission policy are unchanged.

[Stage 09.4B.3](production-execution.md) adds one-shot execution and durable approval
consumption over this unchanged verifier. It does not establish a production issuer,
install trusted bindings or perform full live release verification. Missing GitHub
policy/Caddy provenance evidence continues to block production execution.

[Stage 09.4B.4 installation/integration](production-installation.md) adds exact
bundle review, separate administrative installation, bounded local sources and
cache-only image preparation using the existing executor/release gates. Deployment
owns manager installation/storage/Caddy/networks; Server Infrastructure owns the
prepared host. Independent authentication/attestors, fresh source evidence and Caddy
provenance remain blockers; no production operation was performed.

## Architecture and installation boundary

The existing `ReleaseVerifier` consumes `ReleaseFacts` only from installed trusted
Python collector code. It has no API/file-to-facts deserializer. The default still
blocks with `production-github-ghcr-attestation-verifier-unconfigured`. A Python
object inside the installed controller is trusted code, as specified in
[ADR 0001](adr/0001-production-execution.md); these ports do not sandbox malicious
Python. API claims, fixtures, user booleans and saved JSON cannot install a port.

| Module | Responsibility |
| --- | --- |
| `release_transport.HTTPSRead` | Bounded TLS GET, no proxy/retries, exact hosts, credential confinement, redacted failures |
| `release_transport.GitHubRead` | Authenticated REST reads for the two fixed repositories |
| `release_transport.GHCRRead` | Anonymous public pull token; exact-digest index/manifest/config bytes; existing OCI verifier |
| `release_source.ProtectedSource` | Main ancestry, effective protection, App-bound checks and exact-SHA workflow runs |
| `release_crypto.AttestationVerifier` | Local adapter to existing acceptance `attestation_command`, `verify_gh_version` and `verified_statement`; pinned gh/Sigstore verification |
| `release_collector.AuthenticatedReleaseCollector` | Candidate/content binding; assemble facts only after every source, byte, signature and policy gate succeeds |
| `release.ReleaseVerifier` | Existing independent facts/candidate relationship check; returns `SourceVerification`, never execution authority |

No second OCI mapper, provenance signature implementation, deployment engine or
journal is introduced. `artifacts.verify_oci_bytes` verifies raw bytes and exact
`linux/amd64`, including descriptor sizes and media types. Hash linkage proves
content identity; source authenticity requires the separate signed evidence.

Installation is an explicit trusted-code composition. The opt-in readiness API
below provides the reviewed composition; the preview CLI and workflows do not
install it.
For a reviewed `OperationCandidate`, compose a fresh `HTTPSRead`, a `GitHubRead`
with a separately supplied read-only token, `GHCRRead` over that transport, and an
`AttestationVerifier` with absolute installed binary/root paths and independently
reviewed SHA256 pins. Pass these to `AuthenticatedReleaseCollector`, then install
that collector on `ReleaseVerifier`. No network occurs in these constructors.
Call `ReleaseVerifier.verify(SourceRequest(...))` for the exact candidate. Use a
fresh transport/collector for each collection; no cache fallback exists.

The collector requires a separate reviewed `publication_check: WorkflowCheck`
for the producer job `Publish validated image / publish`, its issuing App ID and
caller `.github/workflows/ci.yml` (`push`). This is distinct from required branch
checks; it need not be a mandatory merge check. The collector also requires
reviewed `WorkflowCheck` objects for **both** repositories
(exact check name, positive issuing App ID, workflow path and `push` event),
`signer_sha`, the exact signer identity
`https://github.com/DanilFaritovich/project-router/.github/workflows/publish.yml@<signer-sha>`,
and a separate exact `slsa_builder` URI. The builder claim inside SLSA and the
certificate-authenticated signer workflow are distinct constraints. These are
installation policy, never manifest flags. `required_checks`/`allowed_builders`
on `ReleaseVerifier` must agree with the installed collector. No example App ID,
manager SHA, trust root or fixture is automatically promoted into production policy.

The manager candidate hashes are SHA256 of the exact raw manifest and
`policy/admission.yaml` bytes at its selected SHA. Default manifest path is
`registry/applications/project-router.yaml`; the only alternative is the existing
disabled `docs/candidates/project-router.yaml`, selected in reviewed installation
code. Verifying that candidate does not enable its application or modify admission.
The full candidate identity remains bound by the existing `ReleaseVerifier`.

## Separately authorized read-only readiness API

`release_readiness.check_release_readiness` accepts only a `ReleaseInstallation`
object constructed in independently reviewed installed Python code. Its fields
bind the complete `OperationCandidate`, both repositories' required workflow/check/
App policies, separate publication job policy, immutable signer identity, SLSA
builder, manifest path and absolute gh/trust-root paths with SHA256 pins. No
production installation/pins are shipped. A dataclass type is not proof of review:
review and installation are the operator's trusted-code boundary under ADR 0001.
Fixtures, request JSON, manifest booleans, arbitrary module/path flags and saved
facts cannot configure this API. Do not copy synthetic test candidates or fixture
identities into an installation.

After separate operation permission and installation review, trusted operator code
may call the following (the variables must already be provided by that installation;
this is not an executable fixture/default configuration):

```python
from scripts.production_integration.release_readiness import check_release_readiness

report = check_release_readiness(
    reviewed_installation,
    authorize_read_only=True,
    github_token=read_only_token,
)
```

The API refuses missing opt-in or an untyped configuration before network reads.
It checks installed binary/root bytes against reviewed pins before any API request;
`AttestationVerifier` rechecks private copies and gh version before cryptographic
execution. Each call constructs a fresh bounded transport, the existing
`AuthenticatedReleaseCollector` and `ReleaseVerifier`; no cached success is used.
Read-only credentials must be supplied separately and must have the permissions
listed below. The API cannot certify a token's grants; possession of a broader
token never authorizes writes. Tokens are never read from environment defaults,
CLI arguments or user gh configuration.

The only output is `readOnly`, `releaseVerified`, `productionAuthorized` and
`blockers`. `productionAuthorized` is **always false**, including on complete
verification success. The first failure returns one allowlisted fixed blocker;
unrecognized errors become `production-release-readiness-unverified`. Reports
contain no candidate/facts hashes, SHAs, image/host identities, credentials, URLs,
API payloads, bundles or verifier output. Nothing is persisted or installed in the
executor. Missing opt-in reports `production-release-read-only-opt-in-required`;
non-installation input reports `production-release-reviewed-installation-required`.

This API is never called by `make check`, `production-integration-check` or PR CI.
No live check was run for this change. Read-only inspection of public workflow/run/
job/check metadata supplied the regression structure; this did not perform source
policy, GHCR or signature verification. A live invocation requires separate
permission, read-only credentials for both repositories, authenticated gh 2.102.0
bytes and an independently verified, pinned Sigstore trust root.

At task start, manager `develop` was
`d1873294a995e8e620b5d189c3797cabd42d01d8`. Its history is not contained in protected `main`.
`production-release-sha-not-on-main` must block; there is no develop/PR exception.
A successful final check requires a **separate reviewed merge into main**, its
required successful exact-SHA push checks, and a newly reviewed candidate bound
to that protected-main manager revision and exact manifest/policy bytes. Merging
this fix into develop alone does not satisfy the gate; no main merge is authorized
by this task.

## GitHub evidence and conservative policy

The authenticated API must establish all of the following for each repository:

1. Exact repository identity and `main` head. Compare selected SHA **as base** to
   that exact head, with `ahead`/`identical` and matching merge base. A SHA from a
   PR, detached history, tag or `develop` is insufficient.
2. Read classical `/branches/main/protection` and effective
   `/rules/branches/main`. GitHub's [effective rules endpoint](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch)
   establishes applicability, including inherited rules, and excludes disabled/
   evaluate rules. Each applicable ruleset must have readable active branch
   details and an explicitly empty bypass list. No glob matching is invented.
3. At least one adequate protection mechanism. Classical protection must enforce
   admins, prevent force pushes/deletion, require a PR, dismiss stale
   reviews, have explicitly empty review bypass allowances and strict App-bound
   required checks. Ruleset protection must prevent non-fast-forward updates and
   deletion, require PRs/stale-review dismissal and strict App-bound
   required checks. Fields that are missing/unreadable block; `protected: true`
   has no evidentiary role. The narrow policy may reject otherwise valid but
   unsupported repository arrangements; it never silently weakens them.
4. Every effective required check must exactly match the reviewed check/App policy.
   Extra mandatory checks require workflow policy review rather than being ignored.
   Select exactly one same-source `push` run by repository/head repository, SHA,
   `main` and workflow path, and require it to succeed. Exactly one required check
   must link to its check suite and succeed with the reviewed issuing App ID.
   Same-name PR checks may coexist only when each independently links to one
   same-repository, same-SHA, same-workflow `pull_request` run in a different suite;
   their result does not replace or override the required push result. PR source
   branch names may differ from `main`. Every same-name check must have the exact
   App and SHA. Duplicate checks within a suite, duplicate/failed push runs,
   ambiguous suite links and unrelated same-name claims block; list order never
   selects a winner. A failed independently linked PR run is not a failed push.
5. Producer publication must be a successful unique same-SHA `push` run of
   caller `.github/workflows/ci.yml` on `main`. Its `referenced_workflows` must
   include the exact producer `publish.yml@<signer-sha>`, SHA and main ref.
   Read `/actions/runs/<id>/attempts/<attempt>/jobs` with complete bounded paging;
   require exactly one reviewed publication job, completed successfully at the
   same run ID, attempt and source SHA. Link its numeric ID and fixed check URL
   to the same-SHA successful check run, exact name, reviewed App ID, run check
   suite and fixed job details URL. No response URL is followed.
   The signed build-origin predicate must still bind **this caller run ID and
   attempt**, selected image digest and source SHA. Neither a successful CI run,
   reusable-workflow reference nor job status alone proves publication.
6. Close the source read round with the same main response and revalidated
   protection. A moving head blocks. API sampling proves current policy as read,
   not historical policy at build time or an atomic GitHub snapshot. Later
   production operation authorization/freshness remains a separate boundary.

Regression fixture `tests/fixtures/release/reusable-publication.json` minimizes
public [run 39776962924](https://github.com/DanilFaritovich/project-router/actions/runs/39776962924):
caller `ci.yml`, reusable `publish.yml`, successful job/check `119819781254`,
check suite `107801677419`, attempt 1. The fixture records API structure only,
not authenticated release facts or production pins. Signed build-origin evidence
is still checked through the pinned verifier, including negative run/attempt,
subject, origin and signature cases; no real signature verification is claimed.

A 404 from classical protection can only be tolerated when adequate effective
rulesets are independently readable. It cannot prove absence or grant permission.
Denied ruleset/bypass details, unknown App bindings, unsupported checks/events,
truncated lists, API denial, timeout and rate limit produce blockers.

The two-repository release collector needs repository metadata, contents, checks,
Actions and attestations **read**, plus
[Administration:read](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
for classical protection. Complete Rulesets bypass evidence additionally requires
Rulesets write access: GitHub [limits visibility of `bypass_actors`](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
to callers with that capability. A read-only grant alone cannot guarantee complete
evidence. Missing bypass details block; never infer an empty list or automatically
widen credentials. GHCR uses an anonymous pull-only token for the fixed public
image repository.

The separately configured [Caddy source App](trusted-caddy-image.md) is installed
only in `DanilFaritovich/portfolio-deployment`. Its source-verification workflows
explicitly request Administration:write and Actions/Checks/Contents:read following
the owner's manual permission change. This elevated grant could change repository
settings if misused, but the verifier uses GET requests exclusively and receives
the token only in its verification step. No Rulesets, branch protection or other
settings are changed. Token revocation remains enabled; no App token goes to
registry operations, artifacts, provenance or other jobs. No production environment,
deployment, secret or package-publish permission is requested for this App token.
API failures or incomplete bypass evidence still block, without policy fallback.

## Approved source policy B — Stage 09.4B.6

The sole maintainer approved B: mandatory PRs and strict CI with zero required
human approvals. The production collector (manager and producer) and independent
bundle reviewer explicitly share `release_source.PRODUCTION_REVIEW_POLICY='B'`.
`ProtectedSource` retains default A for callers requiring at least one eligible
approval. No untrusted selector or automatic fallback exists.

[Trust readiness](production-trust-readiness.md) records fresh authenticated GETs
of both Rulesets and effective main rules after the operator's manual update:
strict up-to-date checks and stale-review dismissal are enabled, approvals are zero,
required checks are App-bound to 15368, bypass is empty and force-push/deletion are
prohibited. These observations supersede the earlier disabled-strict/stale snapshot.
No GitHub Settings write was performed by this task.

B retains all other source, exact-SHA push workflow, publication, OCI, signature,
provenance and build-origin gates described here. It cannot provide independent
human review of changes to code or CI; the maintainer accepts that tradeoff.
Independent bundle/dependency/binding verification and separate operation approvals
remain necessary. Readiness always returns `productionAuthorized=false`.

## Diagnostic preflight record and remaining stage boundary

The preceding read-only preflight used manager main
`9e48475f41050827bcac61ac39d3a61f92f857c2` and producer main
`526c765f33932e163392dd1d512de4aa2f2a5260`. Both compare endpoints returned identical
with matching base/merge-base SHAs; closing reads retained the same heads.
These are historical observations, not fresh release evidence for this PR.

Manager `.github/workflows/offline.yml` had successful same-SHA `push` run
`39853229945` (check `120084630137`, suite `108018386375`) and `pull_request` run
`39853946547` (check `120087128838`, suite `108020410506`), both named
`Offline checks` and issued by App `15368`. This is the duplicate-name case fixed
here; offline regressions exercise it without treating API snapshots as facts.

ProjectRouter caller `.github/workflows/ci.yml`, `push` on main, publication run
`39776962924`, attempt `1`, referenced reusable `.github/workflows/publish.yml` at
the exact producer SHA. Publication job/check `119819781254`, suite `107801677419`,
App `15368`, source SHA and fixed job/check URLs matched. Both required producer
checks succeeded in that run; the caller requires both before publication.
Publication metadata is not cryptographic proof.

Existing `GHCRRead` from that manager main verified anonymous exact-digest OCI
bytes without Docker or image-layer downloads:

| Diagnostic object | SHA256 |
| --- | --- |
| Selected index | `a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853` |
| Unique linux/amd64 manifest | `e24e8a7d68f341199ae90ffba8e15d225c9ce38b7a45ebd547ec918136ce72da` |
| Image config | `115af1631e16774d114aa857130086e0a32711720895195c52d0b30ce68df102` |
| Official gh 2.102.0 linux/amd64 archive | `bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386` |
| Extracted gh executable | `7469124f706944133d6a169691dd1c6c3511b12e85878d255e044e2948df4c9b` |
| Prepared trusted_root.jsonl | `65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c` |

The archive matched the trusted manager-main pin and official immutable
[gh release metadata](https://github.com/cli/cli/releases/tag/v2.102.0). The extracted
binary's version was checked. With a fresh isolated HOME and no credentials,
its official `gh attestation trusted-root` successfully validated the normal
Sigstore/GitHub TUF repositories and produced two roots (34634 bytes).
The sampled TUF timestamp expiries were 2026-10-29T13:30:37Z (Sigstore) and
2026-10-30T01:37:55Z (GitHub). These dated temporary preparation results are not
installed trust pins, perpetual freshness evidence or operator installation review.

Two embedded exact-digest bundles were available for SLSA v1 and build-origin v1.
Their signatures/certificate identities were **not verified**. The decoded SLSA
builder claim `https://github.com/DanilFaritovich/project-router/.github/workflows/publish.yml@refs/heads/main`
remains untrusted payload data, not an independently established builder pin.
No `ReleaseFacts` or successful readiness report was produced. Existing broad
user credentials were used only for explicit GitHub GET diagnostics; they were
not supplied to the full verifier or crypto subprocesses.

A genuine `OperationCandidate` still requires independently established host and
daemon identities plus plan, gateway, runtime, network, storage and recovery
hashes. Those belong to the next separately scoped production-preflight stage
(09.4B.3), with its own authorization for any actual host observations. No dummy
values or saved diagnostic reports may satisfy that contract. A reviewed complete
`ReleaseInstallation`, separately supplied limited read-only credential, and new
explicit operator permission are required before the first full
`check_release_readiness(..., authorize_read_only=True)` invocation. It was not
run in this task. `productionAuthorized` remains **false**; production execution,
GitHub Settings changes and CD remain outside this task.

## Cryptographic evidence

The adapter reuses the pinned acceptance gh **2.102.0**, including the existing
version/security floor. Provisioning is external; this code downloads nothing.
Both executable bytes (max 128 MiB) and trust-root bytes (max 1 MiB) must match
installation pins before execution. They are copied into a private temporary
directory, avoiding execution of a subsequently substituted source file. The
trust root must be independently authenticated, maintained and reviewed for the
applicable signing period; a root downloaded alongside an untrusted bundle is
not authority.

Only embedded bundles from the authenticated producer's exact-digest attestation
endpoint are used; `bundle_url` is never followed. The selected raw OCI index or
manifest bytes are the local gh subject, with the **same** selected digest. No
tag resolution or OCI resolver inside gh can substitute that subject.

Using the existing [gh attestation verification](https://cli.github.com/manual/gh_attestation_verify)
mechanism with local `--bundle` and `--custom-trusted-root` verifies the DSSE
signature, certificate trust/identity and witnessed time (Rekor/TSA as required
by the selected root). Exact repo, signer workflow, immutable signer SHA, source
SHA, source ref and GitHub OIDC issuer are enforced; self-hosted runners are
rejected. Only exit-zero verifier stdout reaches the existing statement selector.
No API JSON can be passed as verified stdout through the installed adapter.

Two separately signed predicates must match the exact image subject:

- SLSA v1, with the separately reviewed builder URI.
- ProjectRouter build-origin v1, with exact repository/SHA, `refs/heads/main`,
  `linux/amd64`, `VITE_SITE_ORIGIN=https://danil-miftakhov.dev`, signed
  `originOutputVerified: true`, publish run ID and run attempt.

The latter is a statement by the trusted pinned workflow, not an independent
inspection of all built HTML. Its value depends on review of that exact producer
workflow. OCI labels, unsigned SLSA, CI status and runtime env cannot replace it.
Workflow/certificate source bindings are enforced by gh, not inferred from the
unsigned API metadata or the statement's self-asserted builder field.

## Bounds, diagnostics and data handling

One transport permits at most 64 requests in a 120-second collection budget,
with socket timeouts capped at 10 seconds and responses capped at 1 MiB. JSON is
parsed by the existing duplicate-key-rejecting bounded parser. List responses
must fit one complete page: fewer than 100 entries, matching `total_count` where
provided, and no pagination Link header. Max 10 rulesets and 10 attestation bundles
are accepted. Exceeding bounds blocks instead of guessing completeness.

Allowed hosts are `api.github.com`, `ghcr.io` and the container blob CDN
`pkg-containers.githubusercontent.com`. Redirects are rejected except a GHCR blob
redirect to that exact HTTPS CDN, with **all credentials removed**. CDN redirects
are not followed. GitHub credentials are confined to `api.github.com`; registry
credentials stay on `ghcr.io`. No user proxy/configuration/credentials or token is
inherited by gh. Local verification has a 30-second process deadline and 4 MiB
stdout cap per invocation; stderr is discarded. Temporary raw bundles and verifier
stdout are never published. The result contains candidate/facts hashes only.

Selected fixed error codes (no remote message/response/attestation text):

| Category | Examples |
| --- | --- |
| Transport/capabilities | `production-release-api-denied-or-rate-limited`, `production-release-api-timeout-or-unavailable`, `production-release-api-pagination-unproven`, `production-release-response-too-large`, `production-release-budget-exhausted` |
| Source/policy | `production-release-sha-not-on-main`, `production-release-protection-insufficient`, `production-release-protection-unverifiable`, `production-release-protection-app-unbound`, `production-release-required-check-policy-mismatch` |
| CI/publication | `production-release-required-check-untrusted`, `production-release-check-workflow-mismatch`, `production-release-publication-unverified`, `production-release-source-changed` |
| Artifact/crypto | `production-release-oci-digest-mismatch`, existing `artifact-*` codes, `production-release-crypto-installation-invalid`, `production-release-signature-unverified`, `production-release-signed-claims-mismatch`, `production-release-builder-mismatch` |
| Incomplete shape/content | `production-release-api-incomplete`, `production-release-evidence-incomplete`, `production-release-manager-content-mismatch` |

No success result is returned after any blocker. No mutation or admission-policy
exception is available. Production execution still has its existing independent
unconfigured privileged/observation/approval gates.

## Caddy provenance decision: blocked

The selected official Caddy 2.11.7-alpine remains index
`sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f`,
amd64 manifest `sha256:04913b1da43339a40ad1ea87827d70a576ac5aa6cf674c6e24fdca675887533a`.
The existing [image lock](../tests/fixtures/caddy/image-lock.json), OCI bytes and
SLSA v0.2 statement prove content linkage. The statement's claimed docker-library
builder and caddy-docker source commit are **not authenticated publisher claims**:
the available statement is plain in-toto JSON, with no validated signature,
certificate, transparency proof or established publisher key. Hashing it through
the index/attestation manifest does not add independent publisher authentication.

No independently verifiable exact-digest publisher/builder mechanism has been
established from these available materials. This is an evidence limitation, not
a claim that no publisher signature could exist elsewhere. No new live Caddy
registry/signature probe was authorized/run. Docker's default
[BuildKit attestations](https://docs.docker.com/build/metadata/attestations/) alone
are not a substitute for signature trust. Legacy
[Docker Content Trust](https://docs.docker.com/engine/security/trust/) signs tag
metadata and is being retired; it cannot be assumed to authenticate this selected
digest without a separately established trusted root and exact signed mapping.

Separate security review could consider:

- Obtaining publisher Sigstore/Notation evidence for this exact digest with an
  independently established publisher/builder trust root.
- Investigating independently trusted signed tag-to-digest metadata, with expiry,
  root and retirement constraints explicitly addressed.
- A distinct official-image registry/source-review policy accepting the lack of
  cryptographic builder provenance, with its weaker guarantee made explicit.
- A reviewed independently signed rebuild, which would require a new image digest
  and separate authorization; it cannot substitute for the current digest.

None is selected or activated. ProjectRouter collector development is independent;
Caddy production admission remains blocked. The 17/17 runtime acceptance result
proves runtime behavior, not publisher authenticity.

## Validation and remaining evidence

Controlled tests cover protected-main success, classic/ruleset protection,
non-main SHA, missing/inapplicable/unreadable policy, App/SHA/workflow/event/repo
substitution, exact manager content, OCI digest/platform gate, unsigned/failed
signature-verifier outcomes, signed origin/source/run/subject substitution,
pinned binary/root substitution, incomplete API pages, timeout/rate limit,
credential-free CDN delivery and unverified Caddy provenance. Integration tests
compose actual GitHub/GHCR adapters, policy, collector and ReleaseVerifier over
controlled API bytes. Crypto process results are controlled fixtures: these tests
exercise the fail-closed integration, **not** a new real Sigstore signature run.
Existing acceptance signature verification remains the cryptographic mechanism.

`make production-integration-check` includes the new tests; `make check` discovers
them offline. Neither calls GitHub, GHCR, Docker, VDS or production credentials.
A real read-only collection needs separate authorization, reviewed installation
pins/check/App/workflow policy and readable protection evidence. It must report
blockers rather than infer success from historical acceptance or current CI.

Remaining production gates include that separately authorized exact-candidate
live collection, independently authenticated Caddy provenance or separately
reviewed security policy, privileged execution installation, authenticated actual
state, operator approval issuance/replay protection, DNS/TLS/storage/recovery and
explicit bootstrap/rollout authorization. None is supplied by this stage.

## Stage 09.4B.5 follow-up

[Production trust readiness](production-trust-readiness.md) records the newer
read-only Rulesets and exact-digest Docker Hub investigation, approved B activation
in 09.4B.6 and ordered release/install actions. Observations supersede older
diagnostic policy snapshots only; they are not live verification facts. Both
production source-verification paths use fixed B; no production authority follows.
