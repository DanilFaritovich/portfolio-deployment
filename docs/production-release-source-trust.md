# Stage 09.4B.2 — Production release and source trust

Status: read-only implementation and controlled offline integration tests. No
real release collection was authorized/run during implementation. Production
execution, approval issuance, deployment and automatic CD remain unavailable.
ProjectRouter and Caddy selected digests, registry and admission policy are unchanged.

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

Installation is an explicit trusted-code composition, not a new CLI or workflow.
For a reviewed `OperationCandidate`, compose a fresh `HTTPSRead`, a `GitHubRead`
with a separately supplied read-only token, `GHCRRead` over that transport, and an
`AttestationVerifier` with absolute installed binary/root paths and independently
reviewed SHA256 pins. Pass these to `AuthenticatedReleaseCollector`, then install
that collector on `ReleaseVerifier`. No network occurs in these constructors.
Call `ReleaseVerifier.verify(SourceRequest(...))` for the exact candidate. Use a
fresh transport/collector for each collection; no cache fallback exists.

The collector requires reviewed `WorkflowCheck` objects for **both** repositories
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
   admins, prevent force pushes/deletion, require a PR approval, dismiss stale
   reviews, have explicitly empty review bypass allowances and strict App-bound
   required checks. Ruleset protection must prevent non-fast-forward updates and
   deletion, require PR approval/stale-review dismissal and strict App-bound
   required checks. Fields that are missing/unreadable block; `protected: true`
   has no evidentiary role. The narrow policy may reject otherwise valid but
   unsupported repository arrangements; it never silently weakens them.
4. Every effective required check must exactly match the reviewed check/App policy.
   Extra mandatory checks require workflow policy review rather than being ignored.
   Each latest check must succeed on the selected SHA and have the expected App
   ID. Its check-suite ID must link to exactly one successful run from the exact
   repository/head repository, SHA, `main`, workflow path and `push` event.
5. Producer publication must be a successful unique same-SHA `push` run of
   `.github/workflows/publish.yml` on `main`. The signed build-origin predicate
   must bind its run ID and attempt, selected image digest and source SHA.
6. Close the source read round with the same main response and revalidated
   protection. A moving head blocks. API sampling proves current policy as read,
   not historical policy at build time or an atomic GitHub snapshot. Later
   production operation authorization/freshness remains a separate boundary.

A 404 from classical protection can only be tolerated when adequate effective
rulesets are independently readable. It cannot prove absence or grant permission.
Denied ruleset/bypass details, unknown App bindings, unsupported checks/events,
truncated lists, API denial, timeout and rate limit produce blockers.

Minimal token permissions are repository metadata, contents, checks and Actions
**read** for the two repositories, plus
[administration read](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
where necessary to inspect classical protection/bypass details. No write,
production environment, deployment, secret or package-publish permission is needed.
GHCR uses an anonymous pull-only token for the fixed public image repository.

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
