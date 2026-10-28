# Production status and delivery evidence

Status reviewed on **2026-10-28**. The operator confirms that the full server
deployment, including the first ProjectRouter rollout, has completed. This
supersedes earlier preparation-only status in the Stage 01–09/C3/C4 documents.
Those documents retain historical checks, decisions and installation procedures;
their original missing prerequisites are not a current host inventory.

## Evidence and its limits

| Evidence | Result | What it establishes |
| --- | --- | --- |
| Operator confirmation on 2026-10-28 | Full server deployment completed | Production installation and first ProjectRouter deployment are complete; this documentation task did not repeat SSH or host inspection |
| [Manager Offline CI 40590290001](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40590290001) | Success, 2026-10-27 UTC | Exact-source checks for `b47e2d684e77022f3b3a58edd906f8c282958d42` |
| [Manager trusted release 40590289784](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40590289784) | Success, same SHA | Signed Manager release publication |
| [Manager Production CD 40590650857](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40590650857) | Success; `deploy` job and deployment step ran | Live Manager CD path succeeded; not proof of an application image update |
| [ProjectRouter CI 40218438797](https://github.com/DanilFaritovich/project-router/actions/runs/40218438797) | Success, 2026-10-25 UTC | Producer CI for `d72f6bbac3d90d246dd5f9751cd2e3a30d871279` |
| [ProjectRouter production CD 40218789081](https://github.com/DanilFaritovich/project-router/actions/runs/40218789081) | Failure, 2026-10-25 UTC | Latest visible automatic application delivery attempt did not succeed; it predates the later Manager fixes |

No successful subsequent automatic ProjectRouter image update or production
rollback drill was established by this review. Successful first deployment does
not supply that separate evidence. Failed bootstrap attempts remain historical
failed attempts even after separately approved recovery; do not relabel them as
successful Actions runs. Private host receipts, journals, credentials and runtime
bindings remain outside Git. The registered digest is desired state, not a fresh
observation of the running container.

## Main history reviewed

The authoritative remote `main` head at review was
[`b47e2d684e77022f3b3a58edd906f8c282958d42`](https://github.com/DanilFaritovich/portfolio-deployment/commit/b47e2d684e77022f3b3a58edd906f8c282958d42)
(release PR #127). Local `main` still pointed at PR #46 (`3d24ca2`); reading that
branch alone would omit the production work. No branches were reset or merged
as part of this review. The documentation branch starts from `develop` at
`f5da16e2e52e6e0088af4ef04bcf63b6f61be370`.

- [Release PR #81](https://github.com/DanilFaritovich/portfolio-deployment/pull/81)
  supplies the immutable reusable CD workflow currently called by ProjectRouter
  (`8447d610f398cc77098f5c95d10d78baba2bf4be`).
- Later main releases include administrative installation/prebootstrap support,
  bootstrap postcondition/recovery fixes and rollout observation/OCI/authority
  fixes. [Release PR #125](https://github.com/DanilFaritovich/portfolio-deployment/pull/125)
  reaches `80547811ce360b8a870f8786e9701b7421e39954`.
- [Release PR #127](https://github.com/DanilFaritovich/portfolio-deployment/pull/127)
  includes first-rollout recovery from PR #126 and original-release verification,
  public-witness rendezvous and fifteen-minute recovery approval from
  PRs #129–#131. Implemented recovery support does not itself prove that a
  particular recovery was executed.

## What happens after a ProjectRouter update

The reviewed producer
[deploy workflow](https://github.com/DanilFaritovich/project-router/blob/d72f6bbac3d90d246dd5f9751cd2e3a30d871279/.github/workflows/deploy.yml)
listens for successful `CI` completion for a **push to `main`** from the same
repository. It calls the pinned Manager `cd-deploy.yml` with `kind: application`.
A PR or a push to a feature branch does not start production application delivery.

The intended delivery chain is:

```text
ProjectRouter main push → successful CI and immutable signed release evidence
  → reusable Production CD → protected production Environment and standing policy
  → exact-candidate approval → host lock and fresh state verification
  → image preparation / scoped application update
  → internal readiness and independent public HTTPS/content witness
```

`CD_MANAGER_CLIENT_SHA` independently pins the client checked out by the workflow;
the producer's reusable-workflow SHA is a separate pin. Neither automatically
tracks Manager `main`. Environment approvals, trust/policy mismatch, missing
evidence or unfinished journal/recovery state can block delivery. The producer
workflow is configured, but the failed application run above means a successful
post-bootstrap automatic update still needs its own run evidence.

Manager delivery follows successful protected-main **Manager trusted release**.
Image-only ProjectRouter updates preserve the gateway; exact no-op checks health
and public acceptance without recreating containers or writing application LKG.
Updates, rollback and recovery reuse the existing executor and journal. Recovery
requires its separately scoped operator authority and never blindly replays a
failed mutation.

## Operating boundary

Deployment completion does not authorize new host operations. Ordinary development
and PR checks remain offline. New SSH/production changes, policy or key changes,
bootstrap/recovery, merges and releases require the applicable explicit authority
or existing narrowly scoped signed policy. Do not rerun first installation on an
already deployed host. Retain original journals, receipts and persistent data.
