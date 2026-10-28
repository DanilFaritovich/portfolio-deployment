# Stage C3 — protected-main release review

> Historical preparation/installation milestone. Full server deployment and the
> first ProjectRouter rollout have since completed per operator (2026-10-28).
> See [current status and delivery evidence](production-status.md). Statements
> below about missing prerequisites or unperformed operations describe the
> original review; do not rerun initial installation on the deployed host.


Status: release preparation only. No merge, tag, GitHub Release, production
installation, image acquisition or rollout is authorized by this review.
Documentation corrections travel through a separate PR into `develop`; its manual
merge and fresh exact-head checks are required before final release approval.

## Exact comparison and coverage

Reviewed on 2026-10-24 (Europe/Moscow), after manual merge of PR #51:

- Base `main`: `3d24ca2cefb723d8c8796940691bbbe31b19c1b2` (PR #46).
- Source `develop`: `4208ff50e71e6b5f50aeeb24340a672258c5428a` (PR #51).
- `main` is an ancestor of `develop`: 0 base-only / 11 source-only commits.
- Full baseline diff: **58 files, 3,492 insertions, 145 deletions**, including
  implementation, workflows, documentation and all changed tests.
- [Complete immutable baseline comparison](https://github.com/DanilFaritovich/portfolio-deployment/compare/3d24ca2cefb723d8c8796940691bbbe31b19c1b2...4208ff50e71e6b5f50aeeb24340a672258c5428a).
  The documentation PR adds README EN/RU corrections and this review; the release
  PR's Files changed is authoritative for its eventual exact head.

| Integrated scope | Release behavior and review coverage |
| --- | --- |
| #47, `ddcc804` | Deduplicated offline checks; read-only real historical Caddy signature/contract QA on PRs; strict signed source/workflow/run/attempt claims. Artifact QA grants no ProtectedSource authority. |
| #48, `9f754dd` | Closed canonical SSHSIG approval envelope, fixed namespace, Ed25519/SHA-512, independently pinned verifier/operator keys, bounded process, repeated under-lock authentication and durable replay protection. |
| #49, `6c3cc5a` + `f708e66` | Complete repeated runtime inventory, local source proofs, exact private-network readiness, independent host policy/prerequisites, canonical journal lease, fixed composition and separately installed cache-only ProjectRouter registry factory. The earlier `[skip ci]` commit is included in final-head aggregate validation. |
| #50, `89177e0` | Fixed Caddy ACTIVE Unix socket, protected directory/socket/peer/runtime identity, bounded double reads versus official adaptation, separately granted reload and unknown-outcome fencing; installer storage addition and disposable integration harness changes. |
| #51, `e26f868` | Single enabled ProjectRouter declaration promoted from candidate to registry, acceptance-path handling and tests; admission policy unchanged. Registration grants no execution authority. |

Review includes positive/negative tests for signatures, changing/unknown facts,
readiness, source proof freshness, missing installed dependencies, cache no-op,
partial failure/replay fences, socket/HTTP framing/ownership/deadlines, and retained
ACTIVE configuration on rejected reload. Offline tests do not prove live host readiness.

## CI and protected source

Effective active ruleset **25628217** covers both `main` and `develop`, with no
bypass actors, PR requirement, stale-review dismissal, force-push/deletion denial,
and strict required **Offline checks**, bound to GitHub Actions App **15368**.
Approved solo-maintainer policy **B** requires zero approving reviews; policy A
remains the low-level verifier default. This review does not weaken either policy.

Exact baseline develop evidence:

- [Offline CI 40040661242](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40040661242), passed.
- [Source App integration 40040661192](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40040661192), passed for that develop SHA.

The source smoke independently authenticates current protected **main** policy/CI
using the repository-scoped App. It does not establish develop as a released main
revision. Release-PR Offline checks additionally demand this successful smoke for
the exact current develop head. Rerun/recheck after any head change.

All workflows were reviewed for triggers, permissions, action pins and credential
flow. PR builds/QA use hosted disposable runners and read-only tokens; no
`pull_request_target` trigger or source App secret reaches PR execution.
Manual application acceptance remains separately confirmed. Source App secrets
are confined to protected develop smoke and protected-main publication. The App
requests **Administration:write** because GitHub needs it to expose ruleset bypass
metadata; the shipped transport issues bounded GETs only and the token is revoked.
This is still a write-capable administrative credential and a trusted-code boundary,
not a read-only permission. It is not passed to image build, registry push or signing.

## ProjectRouter and installation contracts

The [registered manifest](../registry/applications/project-router.yaml) agrees with
[Stage C2 producer verification and isolated acceptance](project-router-acceptance.md):

- Image `ghcr.io/danilfaritovich/project-router@sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853`.
- Producer/source/signer `DanilFaritovich/project-router` /
  `526c765f33932e163392dd1d512de4aa2f2a5260`, `refs/heads/main`,
  `.github/workflows/publish.yml`, run **39776962924**, attempt **1**.
- SLSA v1 and exact build-origin predicate, `linux/amd64`, origin
  `https://danil-miftakhov.dev`, HTTP 8080 `/en/`, user 101:101, read-only root,
  ALL capabilities dropped, no-new-privileges, 16 MiB `/tmp`, preserved HEALTHCHECK,
  stateless storage, no runtime secrets/host ports and one admitted hostname.
- Historical isolated acceptance **39837965499** passed. Neither that report nor
  the Stage C2 verification replaces fresh full production `ReleaseVerifier`.

Both `installation/bindings.py` and `installation/admin_bindings.py` still raise.
Fixed composition is opt-in trusted code, not request-selected configuration.
SSHSIG keys, host policy/prerequisite authority, source/runtime/bootstrap/gateway
attestors, private network/image/container pins and sealed dependency inventory
must be independently installed/reviewed. No keys or production authority are
introduced here. New storage must include the protected `caddy-admin` directory;
existing storage lacking it blocks, with no automatic migration/adoption.

## Effect of a future manual main merge

A main push automatically runs Offline CI and Trusted Caddy image build.
Publication waits for successful **exact main-push SHA** Offline CI (bounded to
40 polls at 15 seconds), then verifies full protected-source policy/head again.
Only then may it push `ghcr.io/danilfaritovich/portfolio-caddy:sha-<main-sha>`, run
exact-digest isolated acceptance, and sign SLSA/input attestations. An uploaded
candidate can remain unsigned if acceptance/signing fails; upload alone is not
release success. No tag or GitHub Release is created by this workflow.

Historical Caddy QA pins digest
`sha256:cd65a6b7daa8029c9f367dcacb9dab14a5eb016c7e584e3ca7f1dbed89ed3800`
at the baseline main SHA. Future publication has its own SHA/digest/run/attempt;
never substitute historical QA for its fresh collector evidence. Publication does
not install, acquire, activate, reload production Caddy or roll out ProjectRouter.

## Local validation

`make fix PYTHON=.venv/bin/python` passed. Targeted registry/admission and
acceptance-contract validation passed; changed-document relative links resolve.
`make workflow-check ACTIONLINT=/tmp/pd-actionlint-1.7.7/actionlint` passed with
actionlint 1.7.7. One full `make check PYTHON=.venv/bin/python` passed:
**2,274 tests** in 101.20 seconds. `git diff --check` passed. Subsequent prose-only
corrections received targeted bootstrap/document checks; executable tests were
not repeated. No local Docker, VM, VDS or production operation was run.

## Release approval handoff

Before manual merge approval, integrate the separate documentation PR, refresh
`develop`, inspect the final release diff/head, and require successful exact-head
Offline CI, independent source App smoke and release-PR checks (including real
Caddy QA/build and runtime/Caddy integration). Prefer a history-preserving merge
for `develop → main`; do not squash away long-lived branch ancestry.

After an independently authorized main merge, verify exact main-push Offline CI
and new Caddy publication/acceptance/attestations. Production installation,
read-only host preflight, cache preparation, first-bootstrap and first ProjectRouter
rollout remain separate operation gates. Fresh protected source/release facts for
both repositories, signed exact candidate, host/daemon/storage/network trust,
backup/console/containment, DNS/TLS and recovery evidence remain prerequisites.
No first-release application LKG exists from which to promise rollback.
