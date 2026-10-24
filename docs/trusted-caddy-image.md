# Stage 09.4B.7 — Trusted Caddy image producer

The producer packages Caddy inside this repository. The existing Deployment Manager
retains runtime/routing/readiness/rollout ownership. No new engine, host operation,
release from develop, Ruleset change or deployment trigger is introduced.

## Gap analysis

| Existing capability | Gap addressed |
| --- | --- |
| Official Caddy 2.11.7 isolated runtime acceptance | Docker Hub builder/signature unverified; that image is not our base |
| Shared OCI mapper, ProtectedSource, pinned gh/Sigstore verifier | Separate Caddy source/image/input verification composition |
| Offline PR CI, application release verification | Read-only producer build and protected-main publication |
| Fixed runtime/security projection | Minimal rootfs and acceptance without image debug utilities |

## Build and upstream trust

[Dockerfile](../images/caddy/Dockerfile) copies the official static **Caddy 2.11.7
Linux amd64 release binary** and its LICENSE into Distroless static-debian13.
There is no compilation, xcaddy, RUN, package installation, shell or curl. The fresh
allowlisted context contains only the verified binary, license and recipe. All
release/source/hash/platform/base identities are in
[inputs.lock.json](../images/caddy/inputs.lock.json).

`cosign 2.6.1 verify-blob` authenticates the release archive with these exact
certificate constraints, beyond comparing a same-source checksum:

- issuer `https://token.actions.githubusercontent.com`;
- signer `https://github.com/caddyserver/caddy/.github/workflows/release.yml@refs/tags/v2.11.7`;
- repository `caddyserver/caddy`, push of `refs/tags/v2.11.7`;
- workflow/source SHA `72dd0fb067f6d7826c7f79907670ba4a713bfe37`.

The [upstream recipe at that SHA](https://github.com/caddyserver/caddy/blob/72dd0fb067f6d7826c7f79907670ba4a713bfe37/.goreleaser.yml)
uses CGO_ENABLED=0 and cosign release signatures. The producer checks the archive,
certificate, signature and extracted binary hashes and rejects a non-amd64 ELF or
an ELF interpreter dependency. It trusts the upstream workflow's authenticated
release, including embedded Go dependencies; it does not claim an independently
reproduced upstream build or independently authenticated transitive source graph.

The base is `gcr.io/distroless/static-debian13@sha256:034df5c1737e8241f6ed6423a12e720ee0f6d6f088609ee172f98f6f2166ef5d`.
`cosign verify` requires issuer `https://accounts.google.com` and signer
`keyless@distroless.iam.gserviceaccount.com`, following the
[Distroless verification policy](https://github.com/GoogleContainerTools/distroless#how-do-i-verify-distroless-images).
Its signed index binds the locked amd64 manifest and all rootfs layers, including
CA roots and timezone data. The shared OCI mapper verifies hashes, descriptor sizes
and config platform. There are no separately fetched runtime dependencies.
This trusts the Distroless publisher's exact rootfs; it does not claim independently
rebuilding Debian packages or verifying a Distroless source-commit SLSA attestation.

Cosign's hash-pinned executable bootstraps Sigstore's signed TUF trust updates;
Fulcio, SCT and Rekor checks remain enabled. No ignore/insecure flags are used.
The downstream gh verifier uses the committed public
[trusted-root snapshot](../images/caddy/github-trusted-root.jsonl), obtained with
`gh 2.102.0 attestation trusted-root`, and hash-pinned in the input lock. Missing or
unverifiable trust material fails closed; updates require review.

The hosted runner remains part of the builder trust boundary. The v2 policy requires
GitHub Actions, `github-hosted`, Ubuntu 24.04 (`ImageOS=ubuntu24` read independently
from `/etc/environment`), Linux/x86_64 and X64/Linux runner metadata. Docker client
and server **28.0.4**, Buildx **0.38.0**, Compose **2.38.2** and all other locked
components remain exact requirements. The default running Docker driver uses the
local Engine over `unix:///var/run/docker.sock`; the fixed builder environment and
redirect rejection remain mandatory. Probes use `/usr/bin/docker` with a minimal
explicit environment. No BuildKit frontend/builder container is downloaded.

`ImageVersion` is observation evidence, not an approved-version allowlist. Both the
process environment and the fixed on-disk `/etc/environment` are read independently.
Each must contain a numeric three-part version with 1–12 digits per part; missing,
malformed, oversized or inconsistent evidence blocks the build. Bounded JSON logs
record each actual value (unrelated environment fields and command output are never
logged). `20261012.334.1`, `20261023.334.3` and previously unknown valid versions are
accepted only when both sources agree and every other gate passes. The signed v2
input predicate records this builder policy and exact component inputs; it does not
claim that a specific VM image version was approved or include the observed version.

### Security review: VM image drift

Removing the exact VM pin loses the requirement to review every complete GitHub VM
image revision before use, including kernel, system packages and unused tools. We
now trust GitHub's ongoing hosted Ubuntu 24.04 image maintenance within this policy.
Exact tool version strings do not authenticate tool executable bytes. A compromised
host could falsify both environment and disk evidence; their agreement detects
inconsistency, not host integrity. This is workflow-authenticated provenance without
a claim of hermetic or independently reproducible builds. Input hashes, upstream
Sigstore signatures, pinned verifier/root, recipe identity, local-backend and redirect
gates, signer/source identity and protected-main publication conditions remain intact.

New publications use `caddy-inputs/v2` and `portfolio.caddy-inputs/v2`, with exact
`builderPolicy` equality. No signed field is ignored. Legacy v1 is accepted in
production only for the known immutable historical digest/source/publication tuple
listed below, using a frozen copy of its complete original inputs. This compatibility
policy is selected before crypto verification from installed constants, never from
unsigned bundle claims; other source/digest/run/attempt combinations require v2.
Historical QA uses its separately hash-pinned original v1 lock. Both paths still
require pinned-root signatures and exact subject, certificate identity, source SHA,
SLSA workflow/builder, invocation and complete input predicate. QA grants no
protected-source or production authority. Additional historical releases require
explicit reviewed compatibility entries; there is no general v1 fallback.

## Workflows, naming and evidence

[caddy-image.yml](../.github/workflows/caddy-image.yml) runs offline checks,
authenticates inputs and builds on fresh GitHub-hosted x64 runners. It checks image
defaults and runs the binary with read-only root, no network, NNP and only
NET_BIND_SERVICE. PR jobs have contents:read; they never publish or sign images.

Only this repository's protected-main push may call
[caddy-publish.yml](../.github/workflows/caddy-publish.yml). ProtectedSource policy B
checks effective main protection, exact-SHA successful `Offline checks` and its
workflow/App identity before upload. Missing/pending/denied evidence blocks release.
Only publication has packages:write, id-token:write and attestations:write.

The guard waits at most 40 observations (15 seconds apart) for the exact main-push
Offline CI run before performing the complete ProtectedSource verification.
PR success cannot substitute for push success. Failed/cancelled CI, duplicate runs,
changed run/attempt, denied/incomplete API evidence or timeout block publication.
Waiting never relaxes source protection, App-bound checks or signatures.

The reusable publisher creates a GitHub App installation token immediately before
`publication_guard`, using official `actions/create-github-app-token` v3.3.0 pinned
to [verified release commit](https://github.com/actions/create-github-app-token/commit/64ab729a6294000f5aa4a4bc0b73d1f32bc45510).
The owner configures repository variable `CADDY_SOURCE_APP_CLIENT_ID` and secret
`CADDY_SOURCE_APP_PRIVATE_KEY`; the caller explicitly passes only the private key
as required reusable secret `source-app-private-key`. Install the App only in this
repository with Administration **read & write** and Actions, Checks and Contents
**read-only** permissions.
The action explicitly requests those four permissions and only the current
repository; missing credentials, unavailable installation or insufficient App
permissions stop publication. No PAT or custom JWT implementation is used.

GitHub [returns `bypass_actors` only with Rulesets write access](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset).
The post-PR #39 [App smoke failed](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39983619292)
with `production-release-protection-bypass-unreadable` because the workflow
requested Administration:read. The owner has since authorized Administration:write
on the existing App. Both
`caddy-source-smoke.yml` and `caddy-publish.yml` now request
`permission-administration: write` immediately before source verification.
The other three requested permissions remain read-only; no additional grants
are requested. Metadata is implicit in GitHub installation tokens.

Administration:write can modify Rulesets, branch protection and other repository
settings if misused. Source verification uses the credential only for GitHub REST
GET requests, never POST/PATCH/PUT/DELETE or GraphQL mutations. The official token
action's credential creation and post-job revocation are lifecycle operations,
separate from repository administration. Only reviewed protected develop/main
push jobs can mint this single-repository credential; untrusted PR jobs receive
no App secret. Never broaden the installation or reuse the token in other steps.
Policy B, exact-SHA CI, App identity checks and the empty bypass requirement stay
unchanged. Missing, null, malformed or nonempty `bypass_actors`, API denial,
rate limits, timeouts and incomplete responses stop verification with redacted
reason codes; there is no fallback to inferred empty bypass or weaker policy.

The ordinary `GITHUB_TOKEN` cannot read the required classic branch-protection
evidence: Administration:read is unavailable in workflow `permissions`. The App
credential does not change the trusted GitHub Actions check App identity
(`15368`) or policy B. Its only consumer is the guard's step-local `GH_TOKEN`;
the private key is an action input, never a build/registry/signing input. Token
creation failure cannot skip the guard or enable upload. Default action cleanup
revokes the token at job completion, including failure; expiry bounds its lifetime
if revocation cannot complete. No App credential enters Docker, GHCR publication,
acceptance, artifacts, logs or provenance. Registry operations continue to use the
separate ephemeral workflow token. Offline tests cover configuration and workflow
failure gates plus real guard/transport denial and trust boundaries; they do not
claim a live App token was minted. Recheck release readiness on the exact develop
SHA after manual merge before preparing the separate develop → main PR.

The checked image transfers through a same-run, attempt-specific immutable Actions
artifact; publication does not rebuild. Loaded config ID/defaults must match.
The repository is **`ghcr.io/danilfaritovich/portfolio-caddy`**. The
`sha-<full-source-SHA>` tag is only a locator and can move on rerun. Production
evidence requires `repository@sha256:<exact-digest>`.

Upload precedes full exact-digest acceptance. Uploaded bytes remain **untrusted
unsigned candidates** until acceptance and both signatures succeed. Failure may
leave unsigned bytes in GHCR; no automatic package deletion/promotion/deployment
exists. Private GHCR pulls use the ephemeral workflow token only at the fixed
scoped token endpoint. Test container processes receive no registry/GitHub token.

After acceptance, official SHA-pinned Actions create two digest-bound keyless
Sigstore DSSE attestations, stored in GitHub and GHCR:

1. SLSA provenance v1; builder `https://github.com/DanilFaritovich/portfolio-deployment/.github/workflows/caddy-publish.yml@refs/heads/main`.
2. `https://github.com/DanilFaritovich/portfolio-deployment/attestations/caddy-inputs/v2`;
   exact input/recipe identities, source SHA/main and successful run ID/attempt.

Build artifacts retain upstream certificate/signature and base verification for
30 days. Publication artifacts retain exact digest, source SHA, signer workflow/SHA,
builder, run identity, acceptance report, input predicate and signature bundles for
90 days. Preserve an audit copy before expiry. No secret, private signing key or
Docker credential file enters image/provenance.

## Runtime compatibility and standard verification

`CaddyRuntime` additionally accepts only this exact GHCR repository by digest.
Command, root UID/GID, `/srv`, XDG environment, three mounts, read-only root/config,
cap_drop ALL, NET_BIND_SERVICE, NNP, networks, 80/443 and loopback-only admin remain.
`/data` holds TLS state; `/config` holds autosave. The image declares no volumes,
healthcheck or entrypoint override.
Its EXPOSE metadata declares TCP 80/443, matching the production renderer so Engine
port publication preserves the verified image defaults; metadata alone publishes nothing.

[Producer acceptance](../scripts/caddy_producer_acceptance.py) inherits the existing
isolated 17-case harness and adds reload of its exact HTTP fixture. Host probes read
real PID 1 facts, traverse actual mounts through `/proc/PID/root` (requiring EROFS
for rejected writes), and join only session-owned network namespaces for HTTP/admin
checks. Peer probes retain a separate container. No debug binary, mount, capability
or host port is added to Caddy. Negative unsafe containers remain unstarted.
Production TLS/ACME, actual storage and gateway activation remain separate gates.

[CaddyReleaseCollector](../scripts/production_integration/caddy_release.py) provides
the separate read-only verification composition for trusted installation code:

- ProtectedSource: main ancestry/protection, exact-SHA CI and the successful
  `Publish trusted Caddy / Publish` reusable workflow job;
- GHCRRead with `repository=IMAGE` and the shared OCI mapper: exact OCI subject
  bytes, amd64 platform/config and existing Caddy image-default constraints;
- AttestationVerifier with `repository=REPOSITORY`: both embedded bundles, pinned
  gh 2.102.0 and trust roots, exact signer workflow/SHA, source SHA/main, OIDC issuer
  and denial of self-hosted runners;
- exact single-subject digest, expected SLSA builder and complete input predicate,
  including the successful publication run/attempt.

Installation supplies trusted ports, check policy and separately reviewed verifier
binary pins. Anonymous GHCR is default; private Caddy reads accept explicit scoped
credentials. ProjectRouter collector/default registry scope and
prepare-project-router-image authorization remain unchanged. Labels, mutable tags,
unsigned statements and saved trust booleans never substitute for this composition.
No new production authorization or apply interface is created.

## Verified and pending

Locally: `make fix`, **72 targeted producer tests**, `make check` (**1954 passed**),
`make workflow-check`, `git diff --check`. Tests cover synthetic authenticated
source/OCI/crypto integration and rejection of wrong SHA/digest/source, missing or
invalid signature, wrong builder/platform/config, incomplete inputs and substituted
publication attempts. Existing runtime negative tests remain included.

Actually verified public upstream: Caddy and Distroless signatures; complete input
preparation rehashing cached downloads, rechecking signatures and mapping amd64 OCI
bytes; extracted binary version v2.11.7. Real cosign checks rejected wrong source
SHA, a forged signature and a missing certificate. These prove input checks only.

The new image has **not been released from main**. Its actual GHCR digest,
signatures and exact-image acceptance remain pending reviewed main integration and
successful publication. Earlier Docker Hub acceptance does not prove this image.
Deployment Manager still needs installed Caddy verification policy/assets, the signed
digest/runtime receipt, independent host/gateway attestors and separate bootstrap/
rollout authorization. No VDS, production Docker, DNS, Ruleset or rollout operation
was performed.
