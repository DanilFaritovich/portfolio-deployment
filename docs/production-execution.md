# Stage 09.4B.3 — Production execution and authorization

Status: implemented and tested offline. No VDS contact, installation, production
mutation, VM run, bootstrap, application rollout or rollback was performed.
`productionAuthorized=false` for the shipped unconfigured installation. Full live
release verification has **not** run; earlier acceptance and diagnostic preflight
are not `releaseVerified` evidence. GitHub policy A/B and Caddy provenance remain
independent operator/security decisions, unchanged by this implementation.

[Stage 09.4B.4](production-installation.md) extends this contract with operator-owned
initial installation (outside the not-yet-installed executor), independent bundle
source/dependency verification and bounded fresh local sources. It adds the separate
cache-only `prepare-project-router-image` scope to the same executor/ledger. The
installation handoff below is historical: Deployment now owns its helper, unit,
storage and Caddy; Server Infrastructure only prepares/maintains the host. Default
administrative/production bindings remain blocking. No VDS installation occurred.

## One-shot boundary

[ADR 0001](adr/0001-production-execution.md) is implemented by
`production_integration.execution.ProductionExecutor`. It uses Stage 03 planning,
Stage 04 gateway candidates, Stage 05 `execute` and its shared fixed Compose
transport, and Stage 06 lifecycle/readiness/rollback/SQLite/application-wide LKG.
There is no second application engine, daemon, scheduler, webhook or CD trigger.
The base disposable lifecycle and read-only production adapter retain their gates.
ProductionLifecycle opens only inside a live consumed grant; no fake test session
is assigned to a production project.

The [launcher](../installation/launcher.py) and [one-shot unit specification](../installation/portfolio-deployment-executor.service)
are installation artifacts, **not installed services**. UID 0 with access to the
rootful Docker socket is root-equivalent host authority. This design does not
claim that a Unix service account or Docker socket is a sandbox. The managed
`ansible` user receives no Docker group/socket access or generic sudo relay.

The unit uses `/usr/bin/python3 -I -S -B` with fixed launcher path: isolated imports,
no site/.pth hooks or system-site dependency fallback, and no bytecode writes.
It has no request arguments, EnvironmentFile, restart,
boot enablement, timer or polling. Its timeout kills the control group; durable
consumption and journal intent still require analysis afterward. Infrastructure
must approve the unit and authenticated invocation mechanism before installing it.

## Candidate and independent evidence

`candidate.CandidatePreflight.prepare(scope)` coordinates four separate roles:

1. Installed read-only ports collect Engine identity, host contracts and actual
   runtime/gateway/readiness or bootstrap observations.
2. An independently installed attestor verifies the exact HostContracts payload,
   authenticated channel, completeness and expected machine/daemon. Production
   source composition uses the existing ProductionStateReader, SourceTrust and
   CompositeStateProvider; BootstrapInput uses its separate attestor. Missing
   attestors, incomplete scope, contradictory inventory, conflicts and stale or
   changing identities block. Sampling must complete within five seconds.
3. Deterministic projection creates OperationCandidate from exact admitted manager
   revision, producer revision, immutable image, raw manifest/policy bytes,
   deterministic plan/gateway/Compose/effective inventory and the independently
   verified network/storage/recovery payloads. Bootstrap hashes actual resource
   facts, IPAM and rendered Caddy runtime; only volatile capture time is excluded
   from state identity. No generated resource ID, substitute hash or synthetic
   route fills an unknown value.
4. Separate operator approval binds that candidate identity. Preparation and saved
   inspection reports never create execution authority.

InstalledContentReader reuses Stage 02 admission on the sealed manager registry
and reads the same selected raw registry manifest and admission policy. The real
registry remains unchanged/empty. A reviewed real manifest with verified digest
must be admitted before using this reader; the disabled candidate outside the
registry does not silently become active. The installed manager SHA must match
the candidate, and existing ReleaseVerifier independently checks both exact
sources/content plus publication, signature, builder, origin and immutable image.

HostContracts is installation-code input, never request JSON. Its three canonical
JSON payloads must contain the exact approved policy and fresh evidence for:

| Payload | Attestor obligations |
| --- | --- |
| network | All Engine networks/attachments/IPAM, host/VPN routes, reviewed proxy/private subnets, host listeners, conflicts and ownership |
| storage | Fixed paths, effective ownership/modes, actual content ownership/absence, capacity/inodes, enforced budgets, certificate storage and backup/restore evidence |
| recovery | Complete unfinished journal and authorization inventory, LKG/baseline identities, backup/console owner and explicitly approved first-release containment |

The attestor must reject incomplete documents rather than approve an arbitrary
nonempty object. Python ports are trusted installation code; typed objects,
nonempty strings and booleans do not authenticate a channel by themselves.

## Transaction and one-time approval

The execution order is fixed:

1. Verify root-owned sealed installation, manager SHA, isolated Python context and
   installed tool/dependency pins.
2. Authenticate the signed envelope with the independently installed operator
   verifier and existing review_approval: exact candidate, operator identity,
   unique 32-hex nonce, not-before/expiry, finite time and maximum one-hour window.
3. Obtain fresh exact-candidate evidence through the existing ReleaseVerifier.
4. Acquire only `/srv/portfolio/locks/host.lock`; verify ownership and fixed inode.
5. Recollect trusted state, re-admit content, re-plan and compare the entire
   OperationCandidate. Reauthenticate the envelope/window after these reads.
6. Validate journal/recovery and operation prerequisites. Durably consume nonce
   and candidate identity in the existing SQLite journal, under the same lease.
7. Record engine intent before the first host mutation. Use the same lease in
   Stage 05 execute, without nested flock or alternate application locks.
8. Run scoped mutation, bounded readiness, complete Caddy validation, retained
   route activation and fresh postconditions; promote application LKG atomically
   only after complete release verification. Record the authorization result.

Only an independently authenticated verifier may return ApprovalClaims. No issuer
or operator key policy is invented here. `approval_verifier=None` fails with
`production-operator-approval-verifier-unavailable`. Unsigned JSON, a fixture, CI,
merge or boolean cannot install that verifier or become a grant. Production
envelopes/credentials are never committed or logged.

Journal schema v2 adds `authorizations` to the **same database**, with unique nonce
and unique exact candidate identity, verified candidate payload, operator/window
and state. Valid v1 schema/history migrates transactionally without replacing
events or LKG; invalid legacy history rolls back the migration. Reserve storage
for migration/SQLite transaction files and back up before approved installation.
Ledger entries cannot be deleted or expired to regain authority.

| State | Meaning and recovery |
| --- | --- |
| no row | No consumption committed; all preflight failures prohibit mutations |
| consumed | Irreversible reservation committed before intent; process death even before intent blocks all subsequent journaled production work |
| succeeded | Entire operation verified and result committed; nonce and candidate remain spent |
| blocked | Existing engine conclusively rejected before intent/mutation; nonce and candidate remain spent |
| recovery-required | Failed/partial/unknown outcome; further production work blocked |

A new nonce cannot replay a previously consumed candidate. Consumed or
recovery-required rows also fence **different** candidates. Journal unfinished
runs retain the existing Stage 06 fence. Failure to persist a result after mutation
returns recovery-required and leaves durable consumption. An OS process-death
regression proves flock release cannot remove that reservation.

`Journal.authorization_recovery()` provides read-only decisions for unresolved
ledger rows, including the pre-intent crash window where Journal.incomplete is
empty. Inspect it under the canonical production lease alongside existing
Stage 06 recovery analysis of attempts, fresh state and LKG. There is no automatic
replay, resume, reset or administrative nonce-release API. Preserve the complete
database, receipts, gateway config/LKG and observations for a separately reviewed
recovery operation. Never use the default disposable analyzer lock as a
production lock.

## Bootstrap and first application rollout

`first-bootstrap` uses the existing plan_bootstrap conflict/IPAM/listener/ownership
checks, plus the independently attested host-route/storage/recovery contracts.
BootstrapExecution journals the manager transaction and invokes only the fixed
ManagedBootstrapPort actions: create absent approved proxy network, initialize
private journal/config storage, create the application private network, officially
validate an empty Caddyfile and start the exact rendered managed Caddy container.
Provisioned directories are prerequisites; bootstrap never adopts or renames a
foreign resource. There is no deletion, pruning, volume removal or cleanup API.
Returned resource IDs and effective ownership/IPAM/runtime/storage/empty active
config require two bounded successful independent verification probes before
manager-bootstrap LKG promotion. The installed resource attestor must also gate
exact Caddy provenance and isolated runtime acceptance; absent attestor blocks.

The bootstrap journal target is the candidate's immutable ProjectRouter release
anchor; runtime/plan hashes bind the actual Caddy image and manager resources.
It is not an assertion that the application ran during bootstrap. A second first
bootstrap is rejected once its LKG exists.

`first-project-router-rollout` requires both fresh independently verified bootstrap
state and a successful bootstrap authorization/LKG for the same machine/daemon.
An old v1 LKG alone cannot satisfy this. It requires a **new** exact signed approval
and an absent ProjectRouter application. The normal engine creates the application
without a public route, checks runtime/readiness and only then uses the existing
ProductionGateway to validate/preserve/reload and verify the intended route.
GrantedGatewayPort admits only that generated complete candidate, fixed storage
paths and an independently attested exact container ID, with renewed daemon
identity checks. Docker argv is fixed; inherited environment is stripped by the
existing bounded transport. Images must already be verified/preloaded: no build
or implicit pull occurs. Image preloading needs its own scoped authorization.

There is no prior application release to restore on first failure. Stage 06
rollback remains conservative and defaults to excluding automatic restoration
without explicit side-effect evidence. Public-route uncertainty retains the old
gateway LKG and requires analysis; it never triggers automatic replay or deletion.

## Exact installation and operator handoff

This is the Stage 09.4B.3 handoff, implemented safely in
[Stage 09.4B.4](production-installation.md) under separate operator authority.
No other repository, users, sudoers, units, socket permissions or host paths were
changed here. Before installation the Deployment operator must:

1. Review root-equivalent execution, the exact unit/start access policy and
   authenticated operator submission. Do not allow callers to choose argv,
   environment, Python modules, executable, Docker endpoint or request path.
2. Build/install the verified exact manager SHA into `/opt/portfolio-deployment`:
   root-owned directories without group/other writes; regular one-link protected
   files, no writable checkout or `.git`, symlinks or unlisted files. Install
   `scripts/` and `schemas/` there, registry/policy assets under `manager/`, pinned runtime
   dependencies under `vendor/`, the supplied `launcher.py`, reviewed `bindings.py`
   and exact trusted validation Caddy at `tools/caddy`. Do not ship tests/fixtures,
   arbitrary editable packages or unreviewed bytecode as trusted dependencies.
3. Supply protected `installation.json` with exactly `manager_sha` and `files`:
   a list of `[absolute_path, lowercase_sha256_hex]` pairs for every bundled file
   except this protected inventory itself. Inventory also pins the resolved
   `/usr/bin/python3` interpreter, system dependency/stdlib installation, Docker,
   the **single** installed Compose plugin, gh and attestation trust-root files.
   Verify artifact/source origin independently before creating the inventory;
   hashing an untrusted checkout is not release verification. Maintain reviewed
   OS/stdlib provenance and protect every import/tool search path.
4. Replace the deliberately blocking bindings with independently reviewed **fixed
   Python wiring**, sealed in that inventory. Compose CandidatePreflight with
   InstalledContentReader, real Engine/machine-ID reader, ProductionStateReader
   and SourceTrust; authenticated host/bootstrap/gateway/runtime/readiness ports;
   existing fresh ReleaseVerifier/AuthenticatedReleaseCollector and pinned crypto
   installation; independent operator verifier; ScopedProductionDocker;
   GatewayExecutionFactory; BootstrapExecution/ManagedBootstrapPort. No ports are
   loaded from request JSON or environment. Missing dependencies must remain
   blockers, including Caddy provenance; no fixture may replace an attestor.
5. Provision the canonical lock directory 0700 and fixed one-link host.lock 0600,
   plus the [reviewed sibling storage paths/budgets](production-preparation.md#canonical-lock-and-durable-storage-contract).
   Provision `/srv/portfolio/requests` 0700. Only an independently authenticated
   intake may atomically publish root-owned 0600 `operation.json`. It contains
   exactly `candidate` (OperationCandidate fields) and `envelope` (base64 signed
   operator envelope, at most 64KiB); total request bound is 128KiB. It contains
   no commands, modules, credentials, runtime secrets or executable paths.
6. Review/install the fixed one-shot unit, authenticate the exact start operation,
   and verify root-only spool, code/tool pins, canonical-lock contention, backups,
   restore/console access, disk/inode budgets and interruption fences before
   enabling any start access. No generic sudoers rule is provided. Each restart
   reads the same spent request and must block rather than retry.

Before the **first bootstrap**, separately authorize installation/host contact and
any read-only release collection/image preload; resolve GitHub source policy A/B
and readable enforcement, Caddy provenance and selected-image runtime evidence;
review actual machine/daemon IDs, host/VPN/Docker IPAM, ports, storage ownership,
backup restore, containment, DNS/ACME/public readiness requirements; install and
validate the independent attestors/operator verifier. Admit the real exact manifest,
collect/review the fresh first-bootstrap candidate, and obtain its signed one-time
approval. Bootstrap success authorizes no application rollout: reobserve, prepare
and approve the distinct first-project-router-rollout candidate afterward.

## Offline validation

`make production-integration-check` includes execution tests. `make check` remains
offline and discovers all tests; workflow-check validates existing workflows.
Tests use controlled installed-code adapters, real temporary flock/SQLite and an
actual disposable child-process exit, never Docker/SSH/VM/GitHub transports.
Coverage includes exact content/state binding, signature/window/nonce failures,
cross-host/scope/candidate substitution, concurrent lock contention, pre/post-intent
death, replay after unknown outcomes, Docker/Caddy/readiness/storage/receipt
failures, transactional migration, hostile argv/artifact/environment/path inputs,
foreign-resource preservation and same-engine application LKG promotion.
