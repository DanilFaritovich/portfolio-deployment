# Stage 09.4B.5 — Production trust readiness

Status: prepared for review; no production authorization, installation, main merge,
Settings change or VDS contact. Policy B is **not approved or activated**.
[Source trust](production-release-source-trust.md),
[installation](production-installation.md) and
[execution](production-execution.md) remain the operative contracts.

## Gap analysis and observed GitHub policy

Read-only GET inspection on 2026-10-23 established:

- Manager develop `bfc4c54e0d5dfadfd76c89ff8bbe77f1594c2ed8`, merge PR #32:
  [Offline CI passed](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39946275697).
- Manager ruleset [25628217](https://github.com/DanilFaritovich/portfolio-deployment/rules/25628217)
  and producer ruleset [25629062](https://github.com/DanilFaritovich/project-router/rules/25629062)
  are active, target `main` and `develop`, have no exclusions or bypass actors,
  and forbid deletion/non-fast-forward updates. Effective main rules agree.
  Classic main protection returns 404 in both repositories; rulesets supply protection.
- Both require PRs with zero approvals, but have **strict up-to-date disabled**
  and stale-review dismissal disabled. Neither satisfies current A or proposed B.
- Manager requires `Offline checks`; producer requires `Quality and static build`
  and `Docker build and container smoke`. All are bound to App ID `15368`.
  Actual check runs identify that App as `github-actions`. Producer publication
  check `Publish validated image / publish` uses the same App. These observations
  must be independently reviewed before installation as trusted policy.
- Producer main was `526c765f33932e163392dd1d512de4aa2f2a5260`; its named checks and
  publication check were successful. This is metadata, not signature verification.

Reproduce using authenticated **GET only** for each fixed repository:
`/rulesets`, `/rules/branches/main?per_page=100`, `/rulesets/<id>`,
`/branches/main/protection`, `/branches/main`, and
`/commits/<exact-sha>/check-runs?filter=latest&per_page=100` under
`https://api.github.com/repos/DanilFaritovich/<repository>`.
The existing bounded ProtectedSource handles effective applicability, details,
main ancestry, exact-SHA workflow/App linkage and read-round closure; no parallel
policy engine, OCI mapper, installer or journal is needed. Historical observations
are not live evidence for release or installation.

## Policy B contract and manual Settings proposal

A remains the default everywhere. `ProtectedSource(github, review_policy='B')`
prepares the sole difference: minimum PR approvals becomes zero instead of one.
PR enforcement, stale-review dismissal, strict checks, trusted App identity,
no bypass, force-push/deletion prohibition, exact main ancestry/SHA and successful
main push workflows remain required. Unknown selectors fail closed. Classic
protection additionally retains enforced administrators and explicitly empty PR
bypass allowances. Ruleset details must remain active/readable with no bypass.

Neither AuthenticatedReleaseCollector nor ManagerBundleVerifier passes B; there
is no CLI, request, environment or ReleaseInstallation policy selector. After
operator approval, a scoped change must wire the reviewed policy consistently
into **both** paths and cover the complete release/crypto integration under B.
Changing Settings alone cannot activate B in the current production verifier.
The prepared low-level tests exercise source collection under B; existing signed
provenance/publication tests remain in the strict production composition.

B loses independent review before protected code/workflow changes: a compromised
maintainer may approve their own source changes through a PR and modify CI itself.
App-bound checks identify the issuer; they do not provide independent human review
of workflow semantics. Main/provenance/signature/installed workflow pins and
independent install/operation approval remain essential. B does not waive any of
those boundaries or select a production approval issuer.

**Pending explicit operator approval**, proposed Settings changes in each repo:

1. Settings → Rules → Rulesets → `ProtectBranches` (IDs above). Keep enforcement
   `Active`, includes `refs/heads/main`, `refs/heads/develop`, exclusions empty,
   bypass list empty. Keep deletion and force-push blocked.
2. Keep “Require a pull request before merging” enabled. For B retain required
   approvals `0`; enable “Dismiss stale pull request approvals when new commits
   are pushed” (`dismiss_stale_reviews_on_push=true`). Do not require last-push
   approval or CODEOWNERS approval for this solo policy. Preserve other existing
   restrictions; these are not reasons to disable PRs or CI.
3. Keep all required status checks listed above. Enable “Require branches to be
   up to date before merging” (`strict_required_status_checks_policy=true`).
   Retain `do_not_enforce_on_create=false`. Each check's expected source must be
   GitHub Actions (`integration_id=15368`), never “any source”. Do not make the
   push-only publication job a PR-required check.
4. Save only after approval. Reread effective rules and full details; compare the
   exact check names/App IDs and empty bypass. Run the reviewed source verifier
   against fresh exact main revisions. An unexpected extra required check needs
   workflow policy review. Disabled/missing/failed CI, substituted workflows or
   Apps, partial API results and unsafe rules must block.

If choosing A instead, additionally require at least one independent approval;
keep stale dismissal and strict checks enabled. No Settings write occurred here.

## Caddy: exact digest investigation and decision

The selected image remains `docker.io/library/caddy:2.11.7-alpine` with index
`sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f`
and amd64 manifest
`sha256:04913b1da43339a40ad1ea87827d70a576ac5aa6cf674c6e24fdca675887533a`.

The [official sources](https://github.com/caddyserver/caddy-docker) describe the
handoff to docker-library/official-images. The selected
[Dockerfile](https://github.com/caddyserver/caddy-docker/blob/9234f5785658cae8129bfae366e196d0670468ac/2.11/alpine/Dockerfile)
is source input, not cryptographic evidence of the resulting image.
[Caddy's Sigstore guidance](https://caddyserver.com/docs/signature-verification)
covers release assets/checksums and their workflow certificate identity. It does
not authenticate this OCI index, its base OS/layers or its Docker builder.
[BuildKit attestations](https://docs.docker.com/build/metadata/attestations/)
attach in-toto metadata; an attached statement alone is not an authenticated signature.

Bounded anonymous HTTPS GET probes on 2026-10-23 (no Docker invocation/pull):

| Probe on registry-1.docker.io/v2/library/caddy | Result |
| --- | --- |
| Selected index, locked attestation manifest and statement | HTTP 200; each raw SHA256 equals the existing lock |
| `/referrers/<digest>` for index and amd64 manifest | HTTP 200; empty manifests in both responses |
| `/manifests/sha256-<hex>.sig` and `.att` for each digest | HTTP 404 for all four conventional cosign tags |
| Attached statement | in-toto v0.1 / SLSA v0.2 JSON; no signature envelope |

The statement claims builder `https://github.com/docker-library` and source
`caddyserver/caddy-docker` commit `9234f5785658cae8129bfae366e196d0670468ac`,
`2.11/alpine`. No verified certificate, publisher public key, independently
established root, transparency proof or authenticated builder identity was found
for this exact image. Registry TLS/digest linkage authenticates neither claim.
The probes cover the standard locations above, not all possible external stores.
No Rekor-wide search or Notary signature verification was performed.
[Legacy Docker Content Trust](https://docs.docker.com/engine/security/trust/)
authenticates tag metadata and is being retired; no trusted signed mapping to
this digest or builder provenance was established. A Caddy binary signature or
another product's Docker signature cannot fill this gap.

**Recommended:** authorize a separate reviewed Caddy build/publish workflow and
its hosting repository (an existing suitable repository is possible; no new
repository is required by this recommendation). Review exact upstream source,
Dockerfile, base/dependency closure and immutable inputs; publish a new immutable
image with signed digest-bound SLSA provenance, pinned workflow/signer/source/
builder, independently maintained Sigstore roots and exact linux/amd64 mapping.
Reuse the existing verifier architecture and standard signature tooling. Signing
or mirroring the upstream digest alone must not claim it was built by that workflow.
This requires an explicit image/repository/trust policy change, runtime/admission
review and repeated runtime acceptance for the new digest. The current ProjectRouter
GHCR collector/cache scope does not accept Caddy; extend the fixed reviewed Caddy
binding separately, without widening ProjectRouter's allowlist.

**Alternative:** obtain from upstream a verifiable signature AND authenticated
builder/source provenance covering the selected exact digest. Independently
establish the publisher certificate/key/root, signer workflow and builder policy,
subject mapping (index vs platform manifest), signing-time/transparency/expiry
rules, then implement the standard verifier adapter with negative tests. This
preserves the image but depends on upstream evidence not currently available.

Operator decision required: select the rebuild route and workflow/repository/image
ownership, or select upstream evidence acquisition and accept that it remains
blocked until evidence exists. No gate exception, producer creation, image change,
crypto implementation or cryptographic success is supplied by this task.

## Remaining dependencies and shortest executable handoff

These are existing contracts with missing authority/evidence, not opportunities
for new generic abstractions. No small integration can truthfully invent their
trust anchors or real host values.

| Dependency | Ready mechanism / remaining work | Where resolved |
| --- | --- | --- |
| Operator approval | `review_approval` and durable executor nonce/candidate ledger exist. Choose independently authenticated signer/issuer allowlist, standard signature mechanism, key custody/revocation and envelope intake; implement/review `verifier.authenticate` outside request input. Wrong signer/candidate/time/replay must block. | Operator decision + reviewed binding, before mutations |
| Administrative install channel | Standalone helper/installer exist; `installation/admin_bindings.py` deliberately raises. Select already authenticated administrator/host channel, review helper and binding externally, confirm exact bundle/manager/machine/pins. No generic sudo relay. | Operator decision, then separately permitted host contact |
| Fixed production composition | `installation/bindings.py` deliberately raises. Review/pin concrete ports and identities; bundle binding must be independently pinned. No request-selected modules. | Reviewed code after policy and actual host identities are available |
| HostContracts / SourceTrust | Kernel/Engine/storage/read-only journal collectors exist; approve network/IPAM/VPN coverage, ownership, quotas, containment and lease-aware recovery attestation. Bind complete actual payloads, port identity, host/daemon and sampling round. | Policy decision + fresh VDS inspection |
| Active Caddy / readiness | Empty-config validation and lifecycle exist. Review bounded container-loopback active-admin reader and independent runtime/readiness attestors; corroborate exact container/image/resources and complete managed universe. Do not infer active state from disk. Before first bootstrap attest real absence; after creation require real active-config/two probes. | Reviewed implementation + VDS observations |
| Runtime dependencies | Exact bundle/dependency verifier exists. Independently authenticate/pin vendor closure, interpreter/stdlib/native libraries, Docker, one Compose plugin, gh, Sigstore roots, ip/ss and Caddy validation binary, plus helper/bindings. Hash manifests alone are not origin. | Workstation review; confirm actual host files after contact |
| Backup/recovery | Canonical lock, journal/LKG and conservative recovery exist. Approve off-host backup destination/retention/encryption, restore and console drill and bounded storage policy; collect real evidence. Empty new storage is not a verified backup. | Operator policy + real host/isolated recovery exercise |

Proceed in this order; each permission applies only to its named operation:

1. **Before any VDS contact:** decide A/B and Caddy route; approve the administrative
   identity/channel and pinned host trust, read-only account and diagnostic command
   scope. Request separate permission for the first read-only inspection. A/B or
   Caddy implementation may continue separately; diagnostics do not require a
   trusted executor installation or fabricated complete SourceTrust.
2. **First read-only inspection, only after permission:** use the existing
   [inspection procedure](production-inspection.md), including its terminal `INSPECT`
   confirmation. Read actual host/machine/daemon/tool identities, routes/ports,
   storage and permissions. Unknown/denied Docker access stays a blocker; approve
   access through the infrastructure owner separately. Retain a private diagnostic
   report; never deserialize it into trusted planner or release facts.
3. **Before initial installation:** complete/review the authority and production
   bindings above, based on actual observations. Independently review/pin the full
   bundle and dependency closure at the released main SHA. Confirm protected paths,
   quiescent window and storage ownership/conflicts. Approve the narrowly scoped
   initial install through the independent administrative channel. Install does
   not start/enable the unit, initialize a fake LKG or run Docker.
4. **After installation:** verify protected files/pins and canonical lock; separately
   review unit recognition/start access, live source collection and signed intake.
   Collect fresh source/host/recovery evidence. Keep incomplete runs fenced; no reset,
   blind retry, volume deletion or automatic data adoption.
5. **Images and activation, separate approvals:** prepare ProjectRouter's exact
   GHCR image cache using its existing scope; acquire/verify Caddy through the
   separately reviewed binding. Approve first-bootstrap with a fresh candidate,
   run two real Caddy postcondition probes and record bootstrap LKG. Then reobserve,
   separately approve first-project-router-rollout, require internal/public readiness,
   DNS/ACME/TLS and retained-config/LKG checks. Cache approval grants no activation.

## Release readiness checklist

- [ ] Recheck latest develop exact SHA and successful Offline CI after this PR merges.
- [ ] Review `origin/main...origin/develop` and the resulting tree diff, including
  workflows, installer/bindings, dependency pins, disabled candidate/admission and
  all trust changes. Resolve the repository's existing license decision before release.
- [ ] Operator manually opens `develop` → `main` PR; approved source policy and
  all strict App-bound required CI must pass. No direct push, automatic merge or release.
- [ ] After the authorized merge, read the actual full main SHA, its ancestry,
  effective rules and exact-SHA successful **push** workflows; do not reuse PR success.
- [ ] Confirm ProjectRouter protected-main producer release, exact GHCR digest,
  OCI platform/config, immutable signer workflow, SLSA builder and signed build-origin
  caller run/attempt. Re-review real admitted manifest/policy bytes; disabled candidate
  remains disabled until an explicitly reviewed registry/admission change.
- [ ] Independently review install bundle and all dependency/helper/binding pins
  from outside the candidate bundle. Build output/report alone is not approval.
- [ ] Separately authorize the first read-only VDS inspection as above. This may
  occur earlier to obtain host facts; repeat fresh observations when needed.
- [ ] Separately authorize the reviewed initial manager installation.
- [ ] Separately approve image preparation/acquisition, Caddy bootstrap, then
  ProjectRouter rollout, with new candidates and fresh evidence at each boundary.

This checklist produces no live release facts or production authorization. A/B
source policy approval is not independent install review or operation approval.
