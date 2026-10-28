# Stage 09.4B.4 — Installation and production integration

> Historical preparation/installation milestone. Full server deployment and the
> first ProjectRouter rollout have since completed per operator (2026-10-28).
> See [current status and delivery evidence](production-status.md). Statements
> below about missing prerequisites or unperformed operations describe the
> original review; do not rerun initial installation on the deployed host.


Status: implemented and verified offline with controlled sources and disposable
filesystems. No VDS/SSH, VM, Docker, systemd, DNS or production mutation occurred.
Production installation and first bootstrap remain blocked by the independent
trust dependencies below. No production binding, approval issuer or CD trigger
is enabled by this change.

## Gap analysis and ownership

The existing Stage 03 planner, Stage 05 engine/Compose transport, Stage 06
journal/LKG/readiness/recovery, ReleaseVerifier/AuthenticatedReleaseCollector,
ProductionExecutor/ExecutionGrant and ManagedBootstrapPort are retained.
The installer and cache preparation do not implement another application engine.

| Existing capability | Actual gap addressed here |
| --- | --- |
| SealedInstallation, fixed launcher and one-shot unit | Deterministic bundle export, independent source/dependency review and standalone administrative installer |
| Fixed production lock and RuntimePaths | Exclusive initial provisioning and preservation checks for manager-owned storage |
| ProductionDockerAdapter, ProductionStateReader, SourceTrust and CompositeStateProvider | Concrete machine/kernel/storage and read-only SQLite sources; independent policy/channel attestors remain required |
| OCI byte mapper and full ReleaseVerifier | Separately approved exact-digest GHCR cache preparation through the same executor/ledger |
| CaddyRuntime, ManagedBootstrapPort, ProductionGateway and GatewayExecutionFactory | Fixed empty-config validation binding; existing network/create/start/reload ports are reused |

Server Infrastructure owns Ubuntu, SSH/users/sudo, Engine/Compose installation,
firewall, hardening, host maintenance and host readiness. Deployment Manager owns
its installation/bootstrap artifacts and lifecycle, manager storage, Caddy,
application/proxy networks, image cache preparation and application operations.
Infrastructure does not install/manage the portfolio gateway, manager unit or
applications. No change to the infrastructure repository is needed or made.
This clarifies the original infrastructure handoff in ADR 0001 without changing
its host-local root-equivalent one-shot execution decision.

## Build and independently review a bundle

Build on an unprivileged workstation from a **full exact commit SHA**. The builder
reads Git objects with replacement objects disabled, rather than executing or
copying a writable checkout. It rejects missing/unknown SHAs, links/submodules,
unsafe paths and dependency hash mismatches. Export selection includes the engine,
production runtime modules and shared acceptance crypto helpers; it excludes E2E,
smoke/diagnostic CLIs, tests, fixtures, Git metadata and the administrative helper.
Runtime dependencies must already be exported into a reviewed vendor tree; no
pip install, network download or dependency resolution occurs during this build.
Vendor pins must cover every file, including native modules/transitive packages.
The official validation Caddy executable is supplied separately and hash pinned.

```sh
make installation-bundle PYTHON=.venv/bin/python \
  MANAGER_REVISION='<exact-full-sha>' \
  INSTALL_VENDOR='/absolute/reviewed/vendor' \
  INSTALL_CADDY='/absolute/reviewed/caddy' \
  INSTALL_DEPENDENCY_PINS='/absolute/reviewed/runtime-files.json' \
  INSTALL_BUNDLE='/absolute/new/bundle.zip'
```

`runtime-files.json` maps `vendor/<relative-file>` and `tools/caddy` to lowercase
SHA256 hex. It is build input, not a source of production trust. The output path
must be absent. Missing inputs fail before scanning directories. For independently
reviewed fixed production wiring, the underlying
`python -m scripts.production_integration.install_bundle` also accepts
`--bindings /absolute/reviewed/bindings.py`; the Make wrapper deliberately ships
the existing blocking binding. No module path is read from an operation request.

Bundle v1 is a deterministic, uncompressed ZIP containing `bundle.json` and
exactly its inventoried regular files. The manifest has `version`, `manager_sha`
and `[relative_path, sha256_hex, mode]` records. Bounds: 128MiB total,
64MiB per file, 4096 entries; no duplicate names/JSON keys, extra files,
compression, links, traversal, bytecode, `.pth` hooks or file/ancestor collisions.
File modes are 0600, with 0700 for the Caddy executable. The one-shot unit's active
directives must match the existing fixed contract, including no boot activation
or restart. The source/dependency file hashes and the independently approved
**whole bundle hash** bind the result. Hashing alone does not establish origin.

A develop build is useful for offline tests and always prints
`productionTrusted=false`. It cannot promote itself to a production release.
An independently reviewed workstation installation of `ManagerBundleVerifier`
accepts only authenticated `GitHubRead`, reviewed manager `WorkflowCheck` policies
and independently established dependency/tool pins. It reuses `ProtectedSource`
to check protected-main ancestry, readable enforcement and exact-SHA App-bound CI
before and after reading the exact commit/tree. It compares Git blob identities
for every selected source, inventory completeness and SHA256 for every runtime
asset. An optional replacement bindings file needs a separate independent pin.
Unknown SHA/content, truncated trees, changed dependencies and missing policy
block. Review code/pins must come from outside the candidate bundle. Neither its
report nor a saved JSON file grants privileged installation or executor rights.
Stage 09.4B.6 fixes approved solo-maintainer policy B in this verifier and the
release collector; low-level ProtectedSource still defaults to A. B removes the
mandatory human approval count and retains every other source/CI/artifact gate.
It grants no installation or runtime authority.

## Resolve initial administrative trust explicitly

The not-yet-installed executor cannot authorize its own initial installation.
The Deployment operator must first independently authenticate the administrative
channel and independently review/pin the standalone stdlib-only
`installation/operator_bootstrap.py` and its fixed `admin_bindings.py`. Install
those two files through that already authenticated administrative channel at:

- `/usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py`;
- `/usr/local/lib/portfolio-deployment-bootstrap/admin_bindings.py`.

Directories/files must be root-owned, protected, with no links or group/other
writes. The administrative mechanism is not supplied by an operation request or
chosen by CI. Its fixed invocation is the installed system Python with
`-I -S -B` and the exact helper path, **without arguments**. The helper rejects a
writable checkout before importing bindings. Never execute a checkout as root,
add a generic sudo relay, use a root shell wrapper or pipe network data to a shell.
No sudoers rule, authentication protocol or new signature scheme is introduced.

The independent channel must authenticate the administrator and obtain explicit
confirmation of initial installation on the exact host. Its reviewed binding
must verify the protected-main manager source, complete dependency/tool/stdlib
provenance and full bundle integrity, then return `InitialInstallationApproval`
binding exact bundle SHA256, manager SHA, machine ID and external file pins.
This trusted-code object is not deserializable approval input. The shipped binding
always raises; a boolean, manifest, merge, CI or configuration file cannot enable it.
The independently pinned helper itself must be checked before its first execution;
self hashing after executing untrusted code would be too late.

Only the authenticated administrative intake may publish the protected input at
`/var/lib/portfolio-deployment-bootstrap/bundle.zip` (root-owned 0600). The fixed
helper reads that file, compares the independent approval and handles bundle bytes
as data. It never imports or executes Python from the bundle. External inventory
must include the resolved system interpreter, reviewed stdlib/system dependencies,
Docker, the single Compose plugin, gh/trust roots, kernel-read tools and both
administrative helper files. The installer verifies supplied external pins and
requires the interpreter, Docker, Compose, gh and both helper pins; the independent
reviewer remains responsible for the complete stdlib/trust-root/dependency closure.
SealedInstallation repeats its existing protections before execution.

`LocalHostSources` invokes only fixed `/usr/bin/ip` (IPv4/IPv6 routes and rules)
and `/usr/bin/ss` (listeners), requiring each exact path in the sealed inventory.
The reviewed Ubuntu 24.04 inspection found `/usr/sbin/ip` to be a symlink to
`/usr/bin/ip`; C4.3 corrects the command spelling without relaxing protection.
Independently verify the target's regular-file identity, root ownership, one hard
link, protected ancestry, exact bytes and package/dependency provenance before
approval. A resolved name alone proves none of these. Symlinks, environment
overrides, request-selected paths and fallback discovery remain unsupported.
The two-second per-command / five-second collection deadlines, 1MiB combined
command output cap and 512-row inventory bounds are unchanged; incomplete or
expired reads fail closed. See the [C4.3 evidence limits and remaining blockers](first-production-installation-readiness.md#c43--fixed-host-ip-path-and-c42-evidence-limits).

No authenticated administrative channel or verifier is installed in this task.
This is a concrete installer with a deliberately missing authority binding, not
permission to run it on a VDS.

## Install/provision without activation or data adoption

The installer writes the exact code to `/opt/portfolio-deployment`, generates the
existing `installation.json` contract (absolute path/hash pairs), and exclusively
creates the existing unit at
`/etc/systemd/system/portfolio-deployment-executor.service`. It never overwrites
an existing unit/install, runs systemctl, reloads systemd, enables a unit or starts
the executor. Unit recognition/start access must be separately reviewed through
the administrative channel. No timer, daemon, background process or Docker action
exists in the installer.

Initial storage uses the existing contract, all root-owned directories 0700:
`/srv/portfolio/{locks,state,journal,requests,caddy-config,caddy-lkg,certificates,caddy-data,temporary}`.
`state` also holds backup/recovery metadata; this creates no claim of a verified
backup. The fixed one-link `/srv/portfolio/locks/host.lock` is created exclusively
as 0600 only for a genuinely new storage root. Journal and authorization ledger
remain in the same `journal/journal.sqlite3`; database creation/migration belongs
to the existing journal under the canonical lease. No empty database, Caddyfile,
operation request, certificate or LKG is manufactured by installation.

All destinations/external pins and conflicts are preflighted before writes. An
existing storage root requires its protected `.manager-storage-v1` ownership
receipt; missing receipt never permits automatic adoption, even for an empty
root-owned directory. Existing nested resource metadata/permissions are bounded
and checked. Name/owner alone does not prove certificate or application content
ownership; that still needs the independent storage attestor.

A repeat verifies exact installation/inventory/unit and preserves lock inode,
SQLite, requests, certificates and LKG bytes. Existing storage must be complete;
a missing canonical lock is never recreated. Existing installs with missing or
changed storage/code, foreign resources, links, changed permissions or a busy
lock fail closed. Installer updates/upgrades and ownership migrations are outside
this initial-install API. Partial failure leaves evidence in place and blocks
blind retry of incomplete state; it performs no rollback deletion. A separate
administrative recovery review must inspect the files and lock before proceeding.
Use a quiescent administrative installation window; subsequent checks take the
same canonical flock. Budgets, quotas, encrypted off-host backups and isolated
restore/console drills remain independent obligations from production-preparation.

## Fresh production source bindings

All constructors are inert. Collection is separately authorized installed code;
CLI previews cannot select Python ports or convert reports into runtime evidence.

| Source / trusted identity | Actual data and completeness | Bounds, binding and failure |
| --- | --- | --- |
| `MachineIdentity` / independently approved machine ID | Protected `/etc/machine-id`, strict 32 hex; ProductionDockerAdapter compares the installed expected ID | 34-byte read; unsafe/missing/mismatched data blocks |
| `installed_engine` / approved local socket and daemon ID | Existing Engine info plus bounded container/network/volume inventory and image inspect; fresh identity checks | Fixed `/usr/bin/docker` and `/run/docker.sock`, existing command/byte/deadline limits; permission failure never means absence |
| `LocalHostSources` / machine+daemon+installed tool pins | Kernel IPv4/IPv6 all-table routes, policy rules, TCP/UDP listeners from exact ip/ss argv; successful complete command output required | 1MiB transport cap, 512 rows/source, 2s/command and 5s complete round; identity recheck; missing pins, unsupported output or timeout blocks |
| `LocalHostSources` storage / fixed manager paths | Actual uid/gid/mode/device/inode and statvfs available bytes/inodes; protected directory ancestry | Same bounded round; missing/unsafe directory fails; independent attestor still checks content ownership, budgets/quotas and backup/restore |
| `ReadOnlyJournalSource` / canonical lease and protected SQLite inode | Existing schema v2, integrity/FK/history and shared ledger validation; incomplete runs and consumed/recovery-required approvals | SQLite mode=ro/query_only, 16MiB, 2s progress deadline and existing row caps; no initialization/migration; any transaction sidecar blocks; missing DB reports unestablished recovery |
| Existing ManagedGatewayReadPort and Caddy runtime comparator / exact host, container, image and source identity | Actual active configuration, retained routes, effective runtime and certificate-storage ownership must be independently attested | Fixed installed transport/SourceTrust required; saved disk Caddyfile, LKG and inspect JSON cannot replace active evidence |
| Existing ProductionProbes/InternalHTTPRead / pinned container and admitted private endpoint | Engine-correlated bounded internal HTTP/image-healthcheck; application runtime corroborated by ProductionStateReader | Existing numeric-address/path/time/output limits, no redirects/proxies/DNS inference; unknown readiness blocks promotion |

LocalHostFacts deliberately carries no `verified` or bootstrap authorization flag.
The independent HostContracts attestor must check the full network/storage/recovery
policy, host network namespace, host/VPN route coverage, complete managed universe,
backup/console and approved containment, and bind the exact sampling round. The
read-only journal port requires the caller's canonical lease; installed composition
must arrange that lease without nested flock. It is also usable for separate
read-only recovery analysis. No journal or local source alone can synthesize a
complete trusted planner snapshot. Runtime/gateway/readiness require independently
installed `SourceBinding`/`SourceTrust` attestors and the existing
ProductionStateReader/CompositeStateProvider composition. Stage B2 supplies the local
source implementations and proof binding below; there is still no automatically
trusted or configured production state reader.


The exact missing live bindings are: an authenticated bounded reader of Caddy's
**active** admin configuration (implemented as an opt-in UDS capability in B3
below; installation pins and real host verification are still required); independent approval/provenance of the
current/historical contract catalog and infrastructure identities consumed by the
new Engine runtime source; and installation of fixed SourceTrust wiring binding
runtime/gateway/readiness payloads to exact ports, host/daemon and sampling round.
PolicyHostContracts now implements local compliance and lease-aware recovery reads,
but still needs independently approved IPAM/host/VPN, quota/backup/restore/console/
containment policy and a fresh external prerequisite-evidence authority.
Stage B3 selects the protected local UDS below. There is no arbitrary container
exec or fabricated empty active-config result. These are installation
and policy dependencies, not missing Docker discovery or a reason to add an engine.

## Separate image-cache approval

`prepare-project-router-image` is an additional **cache-only** OperationCandidate
scope in the same ProductionExecutor, canonical lock and durable nonce/candidate
ledger. It does not authorize bootstrap or application rollout. CandidatePreflight
collects immutable GHCR index/manifest/config bytes using an installed fresh
GHCRRead factory and the existing OCI byte mapper; runtime_hash binds the exact
config/platform evidence. Host/content/network/storage/recovery attestation is
still required. No gateway activation or preloaded-image bootstrap baseline is
needed for this scope, avoiding a dependency on the image being pulled already.

ReleaseVerifier and AuthenticatedReleaseCollector still independently require both
protected-main sources, trusted App-bound workflow/CI, exact publication digest,
SLSA provenance, signer identity and signed build origin. Before cache mutation,
the executor revalidates the full candidate under the lock, reauthenticates the
signed operator envelope, and durably consumes its unique nonce/candidate.
That cache-specific ledger payload is the durable intent; image preparation does
not create an application readiness event or promote an application/gateway LKG.

ImagePreparation permits only
`ghcr.io/danilfaritovich/project-router@sha256:<64-hex>` on `linux/amd64`. Complete
bounded image-cache enumeration establishes presence/absence; inspect failure is
never treated as permission to pull. A correctly cached image is a no-op. An
absent image permits exactly `docker image pull --platform linux/amd64 <exact-ref>`
on the fixed local endpoint (120s / 1MiB output bound, private empty Docker config).
There is no tag fallback, build, credential discovery, prune, deletion or retry.
Two fresh Engine inspections must match the exact verified requested RepoDigest,
config ID, platform and normalized config; pull stdout grants no trust.

Failure/process death after consumption leaves recovery-required or consumed
history, fences every subsequent scope and cannot be replayed with a new nonce.
The same spent candidate stays spent after success/no-op. Diagnose actual cache
and shared journal state under the canonical lease; no reset/retry API is added.
Caddy repositories are deliberately excluded from this GHCR mechanism until
independent provenance policy is approved.

## Caddy and the first bootstrap handoff

Fixed reviewed wiring composes CaddyRuntime and BootstrapValidationBinding with
the independently installed resource attestor, then injects that into the existing
ManagedBootstrapPort/BootstrapExecution. The binding requires the independent
preflight first, validates only the generated exact empty config using the sealed
`tools/caddy` (`adapt` then `validate`, 15s each), and delegates actual created
resource/runtime/active-config postconditions to the independent attestor.
Missing provenance/runtime/ownership proofs still block before resource mutations.
Syntax validation does not establish Caddy image provenance.

Existing manager mutations create only the approved absent proxy network and
ProjectRouter private network with fixed ownership/IPAM, create the exclusive
empty config and use the shared no-pull Compose transport for Caddy. Existing
BootstrapExecution journals intent and verifies two real postcondition probes
before promoting manager-bootstrap LKG. No application runs in first-bootstrap.
After a successful independently approved bootstrap, fresh observations plus a
new signed first-project-router-rollout candidate use ProductionGateway and
GatewayExecutionFactory, retain the old route set/LKG, check application readiness,
validate the complete config, reload only the attested container and verify public
postconditions. Failures retain recovery evidence; they authorize no cleanup.

Caddy's selected official digest/runtime acceptance alone cannot satisfy the
mandatory cryptographic provenance gate. Separate security review must choose:
accepted upstream digest-bound cryptographic provenance; or a separately reviewed
trusted producer rebuilding exact upstream source and publishing signed provenance
and immutable artifacts. The second option needs explicit repository/admission/
CaddyRuntime policy review and cannot be enabled by aliasing a Docker Hub digest
or changing a version string. No such image, policy exception or producer is added.

## Remaining operator prerequisites and acceptance

Before initial installation: establish/review the administrative channel, exact
main release, independent dependency/tool/stdlib provenance, bootstrap helper and
binding pins, fresh protected-main evidence under approved B, protected paths and
quiescent installation window.
Do not run the unconfigured helper or copy a task/develop artifact as trusted code.

Before actual read-only VDS preflight: separate host-contact permission, pinned
administrative/inspection host identity, approved machine/daemon IDs and read access
to Engine/kernel/storage/journal; install/review fixed source composition and
independent attestors. Existing unprivileged SSH access does not grant Docker
permissions; do not extend ansible's sudo/socket/group policy by inference.

Before bootstrap: independently verified image availability (ProjectRouter cache
preparation needs its own approval; Caddy acquisition/provenance still needs a
reviewed separate binding), exact-image runtime evidence, installed SourceTrust/
active-config/readiness/host/bootstrap attestors, independent signed operator
verifier/intake/start policy, readable source enforcement, real admitted manifest,
reviewed IPAM/routes/ports/storage/budgets, backup/restore/console/containment and
DNS/ACME/public readiness prerequisites. Then collect and sign first-bootstrap.
Success permits no rollout: reobserve and separately approve the first application.

Offline tests cover bundle build/review and Make wrapper, changed/unknown source,
archive attacks, protected paths/modes, idempotence and data/inode preservation,
checkout rejection/no autostart, controlled kernel/SQLite reads, missing trust,
empty-config validation, cache scope/digest/platform restrictions, no-op and
partial/dead-process recovery/replay fences. Existing execution tests retain
network/port/storage conflicts, Docker/Caddy partial failure and separate bootstrap/
rollout permissions. No real host readiness, Docker, Caddy or TLS claim follows.

The [Stage 09.4B.5 handoff](production-trust-readiness.md) consolidates the pending
operator decisions, real-host evidence and separate release/diagnostic/install/
image/bootstrap/rollout approvals. Stage 09.4B.6 activates approved B only in source
verification; installation and runtime authority bindings remain unconfigured.

## Operator signature verifier

The [operator approval contract](operator-approval.md) provides closed SSHSIG v1
verification and a separate local inspection workflow. A future reviewed fixed
binding may use `SSHSIGVerifier(installation)`; both shipped bindings still block.
The existing independently approved `external_files` inventory must additionally
pin `/usr/bin/ssh-keygen` and `/etc/portfolio-deployment/operator-keys.json`.
No installer authority, trust key, host permission or production readiness is
established by providing this implementation. Key selection/custody/revocation,
external pins and all prerequisites above remain separate operator decisions.

## Stage B2 trusted components

Status: **verified offline implementation**, unconfigured production authorities.
B1 PR #48 is an ancestor of the B2 base (`3edb3f59f3708631e20bfc78c7f5243c316aa21c`).
No VDS contact, installation, activation, image pull/publication or VM E2E occurred.

| Implemented | Tested offline | Requires operator decision | Requires VDS evidence |
| --- | --- | --- | --- |
| `runtime_sources.RuntimeInventory/RuntimeSource` reuse full Engine discovery, image binding, resource preflight and effective runtime comparator; exact current/historical contract matching, double inventory, unknown resources block | Real discovery over controlled Engine records, actual OCI byte/config-ID binding, old runtime versus changed desired, foreign/denied/incomplete/changing inventory | Independently admit the catalog including prior contracts; approve exact infrastructure identities and image resolver | Complete accessible Engine universe, actual IDs/images/security/networks/volumes; unknown old runtime cannot be reconstructed from desired or LKG |
| `LocalSourceAttestor` binds fixed source instance, returned object, immutable payload digest, round and machine/daemon through existing SourceTrust | Replaced/mutated proof rejection; complete ProductionStateReader/CompositeStateProvider path | Independently review and seal source code/policy/port identities | Fresh local authenticated samples; JSON/README/fixtures establish no authority |
| Existing numeric `InternalHTTPRead` plus strengthened ProductionProbes: pinned network ID, ownership, internal bridge/IPAM/member address before and after HTTP | Bounded admitted port/path; changed IP/container/network and unknown readiness prevent activation/LKG | Approve network ID/subnet and host-local reachability | Actual network membership, running exact container and bounded internal response |
| `PolicyHostContracts` checks independently approved exact network/resource/storage hashes, capacity/inodes, protected contents and real journal/bootstrap authorization | Same lease with real temporary SQLite; missing authority/backup/console, storage/routes/IPAM, identity, stale facts and interrupted journal block | Approve phase-specific HostPolicy and separate authority for allocation, budget enforcement, backup restore, console and containment | Kernel routes/rules/listeners including host networking/VPN; complete IPAM; directory inode/owner/content, quotas/capacity; actual restore/console evidence |
| `ActiveConfigTransport` compares two independent bounded active JSON reads with sealed local `caddy adapt`; ManagedGatewayReadPort retains its existing checks | Altered/foreign/changing JSON, unavailable channel, pins/identity and closed response parsing; official adapter invocation remains bounded | Choose/review local ACTIVE GET channel and independent exact runtime/storage authority; public postcondition authority is also required | Actual pinned gateway/container/image, storage and two matching ACTIVE reads; public readiness/DNS/TLS |
| `composition.build` wires existing sealed installation, SSHSIG, release collector/verifier, host/source/probes/gateway/bootstrap/rollout ports and ProductionExecutor | Inert wiring; real SSHSIG + release/source policy + OCI + coordinator + lock/ledger/lifecycle acceptance | Fixed binding code and independent pins, operator key/custody/revocation, all external authorities; no request-selected module or env hook | Confirm protected installed dependency closure and identities before enabling any intake/start access |
| Existing standalone operator bootstrap, InitialInstallationApproval and ManagerBundleVerifier retained | Existing installation suite: source/bundle review, conflicts, preservation, isolated helper and no autostart | Independent administrative channel/helper/binding/bundle approval outside install bundle | Actual host/machine/tool pins, protected destination and quiescent installation window |

The runtime catalog contains independently admitted immutable application contracts,
including any supported prior versions. Actual Engine runtime must uniquely match a
catalog entry; current desired state never fills an unknown prior spec. Config/secret
delivery remains unsupported. Infrastructure pins include kind, ID, name and facts;
network membership is checked against complete separately classified containers.
Missing pinned infrastructure blocks. An application private network can exist after
bootstrap without pretending the application container already exists. Unknown
managed containers still block; unrelated approved infrastructure is preserved.

HostPolicy is trusted installation-code input, not a schema for promoting an inspection
report. `network_hash` binds complete kernel routing/rules/listeners plus Engine network
and host-networking facts; `resources_hash` binds the full Engine universe for that
operation phase. `storage_hash` binds protected fixed-path metadata/content hashes.
Capacity counters are threshold checks, not volatile candidate identity. Journal data
is read with `ReadOnlyJournalSource` under the existing canonical lease; bootstrap LKG
must match its successful first-bootstrap authorization and machine/daemon. Both
unfinished runs and unresolved authorization consumption block. Request content/inodes
are excluded from candidate identity to avoid self-reference; their protection is
still checked. Journal contents use the existing bounded schema/history checks.
Storage traversal is bounded; no files are adopted, initialized or deleted.

`prerequisites.verify(policy, identity)` must independently authenticate exact policy,
evidence origin, host binding, freshness and revocation. Backup/console/allocation/
budget/containment reference strings cannot implement it. Exact policy hashes enforce
comparison, not independent approval. Actual quotas, encrypted off-host backup,
isolated restore and console drills remain mandatory external prerequisites.

Caddy's [API](https://caddyserver.com/docs/api) exports active state via `GET /config/`.
Its [CLI](https://caddyserver.com/docs/command-line) adapts local documents and reloads
configuration; it supplies no active-config GET command. The shipped
[`images/caddy/Dockerfile`](../images/caddy/Dockerfile) copies only Caddy into distroless,
without curl/wget, shell or an admin-read helper. The B2 baseline admin endpoint was
container loopback. Host loopback/bridge HTTP cannot reach that listener; disk
Caddyfile/autosave is not ACTIVE evidence. `UnconfiguredActiveChannel` therefore
raises. No generic exec, nsenter, extra capabilities, public admin endpoint or
successful placeholder was introduced. Offline adapter tests use controlled JSON;
they do not prove the published Caddy's live admin behavior.

B2 operator handoff originally required selecting a local GET channel. B3 below
selects and implements the permissioned UDS option without an image change.
Installation and operation authority are still not enabled. Runtime/channel identity and storage
must be independently verified before reads; active responses are capped at 1MiB,
one second per GET, with a five-second comparison round. Foreign/unsupported configs
block. GatewaySource additionally requires independent public-route postconditions
before any nonempty route set can contribute release-success evidence.

Run the new acceptance offline with installed pinned development dependencies:

```sh
make fix
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_trusted_bindings.py
```

Acceptance uses the real CandidatePreflight, SourceTrust, ReleaseVerifier and
AuthenticatedReleaseCollector with controlled API/registry/crypto transports, real
OCI hashes, temporary Ed25519 SSHSIG, coordinator, flock, durable SQLite consumption,
existing lifecycle and simulated mutation ports. It covers bootstrap with two
postcondition observations, separately signed rollout, partial bootstrap, changing
state, missing trust, readiness/gateway failures and consumed-nonce fences. This is
synthetic acceptance, **not a deployment or verified production provenance**.

Shortest remaining sequence (each live step needs its separate existing approval):

1. Approve administrative identity/channel, operator key/pins, recovery/backup/
   network/storage policy and the Caddy ACTIVE-channel design. Implement/review
   remaining channel, prerequisite/runtime/bootstrap/public-postcondition authorities.
2. Separately authorize read-only VDS inspection. Establish actual machine/daemon,
   complete Engine, kernel/VPN/listeners, storage/quotas and recovery evidence.
   Translate reviewed facts into fixed phase policies; never import the report as
   an attestation. Resolve denied access through the infrastructure owner.
3. After separately reviewed protected-main integration/release and real manifest
   admission, independently verify the exact bundle, helper, binding and dependency
   closure with ManagerBundleVerifier. Approve InitialInstallationApproval through
   the chosen external administrative channel; install without activation.
4. Confirm installation.verify, protected spool and canonical lock. Separately
   authorize image cache/acquisition and fresh release/Caddy runtime verification.
   Collect first-bootstrap candidate under that lock, independently sign and submit
   through the approved intake/start channel; require two actual postconditions.
5. Reobserve the resulting baseline and approve its actual network/resource IDs in
   the next fixed phase policy. Prepare and separately sign the first ProjectRouter
   rollout; require internal readiness, full active gateway verification and real
   public DNS/TLS/path postconditions before LKG promotion. On ambiguity retain
   evidence and stop for recovery review; no replay/reset/deletion is available.

Both shipped `installation/bindings.py` and `installation/admin_bindings.py` still
raise. SealedInstallation.verify is unchanged. The fixed recipe is not installed,
and no default working production authorization exists.


## Stage B3 trusted Caddy ACTIVE admin channel

B3 starts from merged B2 PR #49, `2293e4024237dca154312626e6dbf68d9b885df5`.
The distroless image, its Dockerfile/input pins, signature requirements, capabilities,
SealedInstallation, SSHSIG and SourceTrust are unchanged. Shipped bindings still
raise; providing this implementation does not enable bootstrap or rollout.

### Socket, mount and access contract

| Item | Required contract |
| --- | --- |
| Host directory | Fixed `/srv/portfolio/caddy-admin`, root:root, exactly 0700; protected root-owned ancestry with no group/other write and no symlinks |
| Container mount | Fourth bind mount, read/write, rprivate, `create_host_path: false`, destination `/run/portfolio-caddy-admin`; exact source/destination/options are compared with Engine |
| Admin listener | `unix//run/portfolio-caddy-admin/admin.sock` with explicit `|0600` permissions; no admin TCP listener or published admin port |
| Socket inode | Only `admin.sock`, socket type, root:root, exactly 0600, one link; symlink, foreign content and replaced device/inode fail closed |
| Local controller | Existing sealed root executor; connects through held NOFOLLOW directory fd, checks Linux SO_PEERCRED against the exact container's host PID and UID/GID 0:0 |
| Runtime pins | Independently installed container ID, Caddy OCI evidence/image/config ID/version 2.11.7 and proxy network ID; complete existing effective runtime/mount/ownership checks before/after each GET and comparison |
| Observation capability | Fixed HTTP/1.1 `GET /config/`, no request-selected path/URL/method/headers/body, redirects, proxy, compression or fallback to disk/autosave |
| Bounds | 1MiB decoded response, 16KiB headers, bounded chunk framing, absolute one-second GET deadline; strict status/framing/JSON and duplicate-key rejection; two equal reads matching official adaptation within five seconds |
| Reload capability | Separately granted `first-project-router-rollout`, same endpoint at `unix//srv/portfolio/caddy-admin/admin.sock|0600`; only sealed `/opt/portfolio-deployment/tools/caddy reload --address ...` and exact candidate bytes |

`RuntimePaths` gains `admin_socket_directory`, accepted only at that fixed path
(and required in the closed offline runtime JSON contract). `CaddyRuntime.render()`
gains the fourth mount. `gateway.content()` changes both empty and full Caddyfiles;
ACTIVE adaptation, bootstrap validation and runtime comparison consume that same
renderer. The optional fixed-composition `active_pins=(container_id, network_id)`
constructs `UnixActiveChannel` and `PinnedCaddyRuntime` with the composition's same
Engine; it requires explicit empty legacy channel/runtime arguments and cannot be
combined with another channel. These are trusted installation-code inputs, never
operation request fields or environment hooks. Fixed production paths still apply.

Installation storage inventory includes the new directory. Initial installation
creates it privately; an existing installation missing it is refused without repair
or automatic migration. The directory is not shared with application containers,
certificate data, config or LKG. Bootstrap requires it empty; observation never
creates, chmods, chowns, replaces or removes a socket. Existing protected socket
metadata can be inventoried without opening it as a file. Socket and directory
inodes join independently reviewed phase-specific storage policy; a restart or
replacement requires fresh pins/policy/evidence, never silent adoption.

Read proofs expire after five seconds. Granted reload checks fresh runtime evidence
around publication/dispatch, retains exact candidate/CAS, durable old config receipt,
canonical lease and existing journal. Every error after disk publication is an
unknown outcome. Failed reload cannot promote application LKG and consumed approval
cannot be replayed; no automatic recovery/reset/reload is introduced. ACTIVE
postconditions remain required before the existing executor can report success.

### Verification and operator handoff

Official references: [admin Unix listener](https://caddyserver.com/docs/caddyfile/options#admin),
[GET /config/](https://caddyserver.com/docs/api#get-configpath),
[reload --address](https://caddyserver.com/docs/command-line#caddy-reload),
[v2.11.7 permission implementation](https://github.com/caddyserver/caddy/blob/v2.11.7/internal/sockets.go),
[listener reuse/chmod](https://github.com/caddyserver/caddy/blob/v2.11.7/listeners.go),
[admin host checks for Unix sockets](https://github.com/caddyserver/caddy/blob/v2.11.7/admin.go).
Unix access control is filesystem-based; Caddy does not provide a GET-only Unix
listener. The Python observation port exposes only GET, while possession of the
socket by a trusted root process remains full Caddy administration authority.

A local disposable feasibility run used the official v2.11.7 binary with SHA-256
`678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`
(from the unchanged image input lock). It confirmed real AF_UNIX HTTP/1.1 GET,
0600 mode, ACTIVE JSON equal to adaptation, reload changing ACTIVE through the same
endpoint while retaining the socket inode, and invalid reload preserving ACTIVE.
It used no TCP listener, Docker, DNS, certificates or production paths. Official
adaptation also passed for final generated empty/full-route and HTTP-only acceptance
configs. This local run did not establish root-owned container mount reachability.

Offline validation: `make fix`, targeted negative/capability/executor tests,
`make workflow-check` with actionlint 1.7.7, one `make check` (**2271 passed**),
and `git diff --check`. Socket type/owner/mode/symlink/inode, peer identity,
container/image/mount/network/config changes, stale evidence, timeout, oversized
responses, malformed HTTP/JSON and denied reads fail closed. A real executor with
a controlled reload failure retains journal evidence, refuses LKG and fences replay.
Mocked transport tests are not evidence of a working live Caddy channel.

The existing explicit disposable runner acceptance now checks the real bind-mounted
UDS using the same bounded HTTP engine, socket metadata and pinned peer PID. It
compares GET with actual Caddy adaptation, reloads an HTTP-only candidate, checks
ACTIVE changes with a stable socket inode, and verifies both malformed reload and
an actual `/load` provisioning failure on the read-only mount leave ACTIVE intact.
The published distroless ProducerHarness inherits these checks without adding tools
to the image. Production GET/reload uses neither curl/wget nor exec/nsenter/shell.

On the existing guarded ephemeral GitHub-hosted Linux runner only:

```sh
make caddy-integration-test PYTHON=python \
  CADDY_TEST_ENDPOINT=unix:///var/run/docker.sock \
  CADDY_TEST_AUTHORIZE=disposable-caddy-integration
```

Default `make check` never runs it. This harness uses a separate test project/network,
private temporary mounts and zero published ports, without ACME or production DNS.
The historical pre-B3 loopback acceptance receipts do not prove the new UDS contract.
The PR's new runtime acceptance report is the evidence for actual bind-mount operation;
exact published distroless acceptance remains separately gated by the existing producer.

Still blocked: independent installation approval and fixed production pins/policies,
Caddy provenance/runtime acceptance for the selected published digest, prerequisite
backup/console/allocation authorities, bootstrap and public-route postconditions,
protected release/admission, read-only VDS inspection and separately authorized
install/bootstrap/rollout. No VDS contact, production install, publication or
activation is performed by B3. No image republish is necessary for this design.
