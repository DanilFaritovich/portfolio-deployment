# Stage 06 — Readiness, Journal & Recovery

Stage 05 PR #5 and Stage 06 PR #6 are merged into `develop`. Stage 06 extends that executor with an
**offline-tested lifecycle**, not production deployment. No real Docker mutation,
local smoke, HTTP transport, Caddy reload, SSH/VPS, DNS/TLS or CD was performed.
Application/admission v1 and Stage 03–05 blockers remain unchanged. Real registry
and admission policy remain empty.

## Integration and APIs

| Module | Responsibility |
| --- | --- |
| `scripts/readiness.py` | `ReadinessPolicy`, `ProbeAdapter`, `ProbeResult`, `verify_readiness` |
| `scripts/composite_state.py` | `SourceEvidence`, `CompositeSample`, `CompositeStateProvider.refresh()` |
| `scripts/deployment_journal.py` | `Intent`, `Journal`, `locked_journal`, closed schema v1 and transactional state transitions |
| `scripts/deployment_lifecycle.py` | `DeploymentLifecycle`, `runtime_identity`, pure `plan_rollback`, `analyze_recovery` |
| `scripts/deployment.py` | Existing `execute(..., lifecycle=None)` orchestration, preflight, scoped adapter and host lock |

Construct desired state through Stage 02 admission / Stage 03 adapters. Pass a
`DeploymentLifecycle(private_absolute_directory, probes, ...)` into the existing
`execute` API with a trusted `CompositeStateProvider`. The lifecycle never replaces
planning or creates another Compose command surface. Stage 05 without lifecycle
retains its unverified result semantics. Lifecycle currently requires an explicit
disposable session; production projects remain rejected by the real adapter.
There is no apply/recover CLI or Make target. Tests supply fake readers/adapters.

A lifecycle instance represents one run. Its UUID hex `run_id` plus component name
identifies each component attempt. Reusing an ID cannot replay mutations. Any
unfinished run blocks subsequent journaled runs, including another application;
Stage 06 deliberately requires operator investigation rather than automatic retry.

## Readiness

Trusted controller configuration defaults to a 60-second component deadline,
2-second interval, two consecutive successes and a 2-second per-probe timeout.
Limits: deadline 1–300 seconds, interval 0.1–30 seconds, successes 2–5,
probe timeout 0.1–10 seconds; the success sequence must fit the deadline.
Configuration is not accepted from application manifests or untrusted PR input.

The trusted adapter receives the admitted component (including HTTP port/path),
pinned actual container identity, remaining bounded timeout and
`follow_redirects=False`. HTTP requires status 200–299 without redirects; worker
readiness requires the actual image healthcheck status `healthy`. A successful
HTTP response never substitutes for a worker healthcheck. Missing data, exceptions
and timeouts cannot verify readiness. No response bodies or Docker health logs
are returned. There is no dependency on in-image curl/wget or extra host ports.
Real transports are intentionally deferred to Stage 07; their implementations
must honor timeout, identity and redirect requirements.

Evidence distinguishes `running`, `ready` (one successful probe), `verified`
(required sequence), `failed` and `unknown`, with probe/consecutive counts and fixed
reason codes. Deadlines and iteration caps bound retries. A controller crash or
late response does not count as success. Each dependency must have verified
readiness in this run and is rechecked before its dependent may proceed.

## Lifecycle and release acceptance

Component transitions are:

```text
prepared -> applying -> verifying -> succeeded
                |           |
                v           v
        outcome-unknown    failed -> rolling-back -> rolled-back-verified
                |             |            |
                +-------------+------------+-> recovery-required
```

No-op components go from prepared to verifying without a mutation. Intent for all
components is committed before the first mutation; applying/rollback intent is
committed before each corresponding `adapter.up`. Preflight and authorization are
rechecked before mutations. After mutation, fresh discovery must corroborate the
intended effective runtime, exact ownership and verified OCI identity. Readiness
uses a pinned container and fresh rediscovery afterward rejects replacement.

The **run** remains active even when individual components succeeded. Before
acceptance all components are verified again. Public applications additionally
require intended active routes and fresh gateway/upstream/path verification from
the gateway source. There is no Caddy reload. Missing gateway evidence leaves the
run recovery-required; component readiness alone never updates last-known-good.
Run acceptance and every component LKG pointer update occur in one transaction.
Partial deployment and verified rollback are not release success.

A fully restored application can instead finish the **run** as `recovered`.
This is terminal for `Journal.incomplete()` but never sets `releaseSuccess` or
promotes LKG. Component execution results still describe the failed attempt
(including `rolled-back-verified` and skipped operations); that result alone does
not prove run recovery. Only the journal's committed `recovered` status does.

Completion is restricted to the current uninterrupted attempt with at least one
`rolled-back-verified` component. The entire intent set must match the preceding
accepted application's component set, previous images and runtime/scope identity.
Other components must be untouched prepared intents or successful baseline no-ops;
a component still running a newly deployed image prevents completion. The lifecycle
reconstructs the whole prior LKG application, then obtains fresh ownership, exact
image/effective-runtime and bounded readiness verification for every component,
including skipped components and dependencies. A final coherent composite round
must corroborate all pinned container identities and required gateway/upstream/path
checks. Missing or conflicting evidence leaves the run blocked.

`Journal.recover()` is the internal persistence step after these trusted lifecycle
checks, under the existing host lease. One `BEGIN IMMEDIATE` transaction verifies
SQLite integrity/foreign keys and journal/event/LKG consistency, rechecks the full
baseline, writes each component's `recovered-verified` / `recovery-verified` event
with fresh readiness counters, and changes the run to `recovered`. It validates
the resulting records before commit and never writes `good`. Original target
images and all earlier events remain. On reopening, recovered history is checked
against its preceding accepted run, even after a later deployment advances LKG.
The table schema remains v1; older readers reject the new closed states.

Known Compose errors can still leave partial changes. Unknown outcomes trigger
rediscovery and require recovery analysis; the executor never replays that
mutation. Later components remain blocked. Unattempted components retain prepared
intent, making partial progress inspectable after interruption. An I/O/journal
failure fails closed; if mutation already happened, the last durable intent
remains available after storage is repaired.

## SQLite durability and security

The standard-library SQLite database is `journal.sqlite3` in an operator-selected
private absolute directory. Parents must already exist. Relative paths, parent
traversal and symlinks in any segment are rejected through a dirfd/NOFOLLOW walk.
Ancestors must belong to the effective operator or system-root identity (including
UID-mapped root) without group/other write, except the system-owned sticky temp
directory. The leaf must be operator-owned and private (0700). Database and known
sidecars must be private regular single-link files owned by the operator; symlinks,
hardlinks, devices and unsafe modes are rejected. Opening uses the pinned directory.
Controller/host administrators and the same OS identity remain trusted.

The schema contains `runs`, component `attempts`, ordered `events` and `good`
(application/component pointers). Rows store revision, previous/target immutable
pull references, a configuration/scope hash, closed states/reasons, timestamps and
readiness status/counts. Runtime identity covers the whole image-excluded
application specification, storage, mounts, derived networks and Compose project
scope. It contains no effective secret values. Reference IDs alone never prove
configuration identity; Stage 05 still blocks all unresolved runtime references.

Schema initialization, state/event writes and promotion use `BEGIN IMMEDIATE`.
`user_version=1`, application ID, exact schema, SQLite integrity/foreign keys,
record bounds and event-chain/current-state agreement are checked at opening.
Unknown versions and incomplete/corrupt records fail closed without repair,
truncation or schema migration. Limits are 16 MiB per database/sidecar, 10,000
attempts/runs and 100,000 events; retention/export/compaction need a future reviewed
operator procedure. No automatic deletion or retention job exists.

SQLite uses DELETE rollback journal, foreign keys and `synchronous=FULL`. This
provides SQLite transaction/crash durability on a functioning local filesystem
that honors locking and fsync. It does not promise persistence through disk loss,
unreliable storage, a full disk, or a host failure that violates those assumptions.
No NFS/shared-host journal support is claimed. Provision storage and test recovery
before production use; a journal is neither live state nor a backup.

The journal requires an active lease from the existing host-wide flock; lifecycle
writes, verification, rollback and recovery analysis remain under that lock.
Never unlink the fixed lock inode or vary the production lock path. The lock does
not control foreign Docker clients, host administrators or other hosts.

Only validated identities, hashes, fixed codes and bounded counters enter rows.
There are no raw errors, logs, HTTP bodies, environment values or secret material.
SQLite files and sidecars are Git-ignored; runtime journals should live outside
the checkout. Do not commit observations or copy a live SQLite file as a backup.

## Rollback policy

One automatic rollback attempt is possible only after a known failure of a
stateless image-only update. All of these must hold:

- A confirmed application LKG identifies the exact previous immutable image.
- Fresh Docker evidence verifies the failed target's ownership/effective runtime
  and confirms the old image is preloaded, available and verified for the platform.
- The whole application configuration and execution scope match LKG: storage,
  mounts, networks, dependencies and runtime reference configuration are unchanged.
- There are no persistent volumes or runtime references.
- Trusted controller policy explicitly asserts migrations and unknown side effects
  are excluded (`side_effects_excluded=True`); default is false.
- The existing adapter authorizes the exact scoped project and service again.

The same Stage 05 `up` operation restores only the previous image; no pull, down,
prune, volume deletion or database downgrade exists. Fresh discovery, readiness
and required gateway verification must then pass. Failure, timeout, missing old
artifact or inability to prove the failed target yields recovery-required. A
stopped/unidentifiable target is conservatively escalated, not guessed at.
Persistent, configuration, storage and migration recovery require a separate
policy and data/backup procedure. Restoring one component does not restore an
entire partially updated application. LKG remains the previous accepted run.
Only the full in-process recovery acceptance described above can unblock the next
deployment; successful component rollback alone cannot. Unknown outcomes, crashes,
partial restoration, failed readiness/gateway checks and missing complete trusted
sources remain blocked. Existing recovery-required runs are not retroactively
closed. There is no replay/resume, journal deletion or operator reset mechanism.

## Crash recovery and complete observations

`analyze_recovery(directory, provider)` acquires the same host lock, validates the
journal, reads unfinished runs and refreshes the composite provider. It compares
recorded intent/LKG with current ownership, immutable image and effective runtime.
It returns deterministic decisions: target observed but verification required,
previous LKG observed but verification required, runtime unverified or fresh state
unavailable. Every decision remains recovery-required. Repeating analysis writes
no decisions, promotes no LKG and runs no mutation or retry loop.

Even a crash between intent and Docker invocation cannot establish whether the
invocation occurred. Matching target state after a crash does not prove readiness.
Stage 06 ships no operator acknowledgment/reset, restart-resume or post-crash
mutation API. Preserve the database and investigate; a separately reviewed
recovery/authorization procedure is needed to resolve unfinished runs. Never
clear journal entries to bypass uncertainty or blindly rerun a timeout.

`CompositeStateProvider` requires a freshly collected coherent round containing
Docker, gateway, runtime configuration and readiness source evidence. Each source
asserts complete/trusted scope and a bounded capture time (default max age 5s,
configurable up to 30s); mixed rounds, stale/missing sources and duplicate Docker
identities block. The full Stage 03 snapshot is validated separately and its flags
are never promoted. Docker/runtime claims must agree when the executor verifies
components. Readiness-source scope is not itself release readiness verification.

Readers are trusted controller interfaces, not attested protocols. Tokens correlate
a sampling round; they cannot authenticate a forged fixture. There is no real
reader in Stage 06. Docker-only `LocalStateProvider` remains incomplete/untrusted;
SQLite is never an observation source. Missing real gateway/runtime observations
therefore keep ordinary live execution blocked.
They also prevent live confirmation of `recovered`: the new terminal path is
exercised only through trusted offline fakes until real providers are separately
implemented and accepted. A journal or rollback command exit code cannot supply
the missing evidence.

## Validation and remaining stages

`make check` stays completely offline; tests use fake transports/providers and
real temporary SQLite. Coverage includes readiness failures/timeouts/dependencies,
transaction rollback and atomic LKG, crashes on both sides of mutation, journal
write failure, partial rollout, constrained rollback, unknown outcomes, lock
contention, corrupt records/unsafe paths, foreign/untrusted observations and
repeatable recovery analysis. Passing fixtures proves no live acceptance.

Stage 07 must implement and verify real isolated HTTP/Docker transports, coherent
Docker+Caddy observation, gateway checks, interruption/recovery drills and data
preservation. Stage 08 owns offline CI and reviewed automation/source gates.
Stage 09 needs separately authorized host integration, production operation and
runtime-configuration contracts, artifact/provenance/retention policy, backup and
recovery ownership, DNS/TLS acceptance and a first controlled rollout. No stage
implicitly authorizes another, production apply, merge or release.

## Stage 07 integration

The [disposable VM harness](local-e2e.md) supplies test-only real transports and a
complete bounded session reader, reusing these hooks and SQLite without creating
another engine. Production transport/authorization remain unavailable. Test-only
loopback Caddy activation is external to the base lifecycle, after pinned readiness.
Crash analysis still cannot replay or automatically complete recovery. Real E2E is
not-performed; fixture digests must be independently built/published and confirmed.

## Stage 09.3 integration

Stage 09.3's [integration rehearsal](production-integration.md#lifecycle-and-stage-094-boundary)
reuses this lifecycle/journal through the same executor, with fake Docker/gateway
ports and real temporary SQLite. Readiness precedes route activation and full
postverification precedes LKG promotion. Failed/unknown activation cannot promote
LKG. Production write approval and live source transports remain unavailable;
inherited test-only lifecycle gating is retained. Existing rollback and crash
recovery limits are unchanged.
