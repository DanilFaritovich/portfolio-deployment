# Stage 09.4B.4 — Installation and production integration

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
GitHub policy A/B remains undecided; this verifier does not weaken that gate.

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
ProductionStateReader/CompositeStateProvider composition. These dependencies are
still missing; there is no automatically trusted production state reader.


The exact missing live bindings are: an authenticated bounded reader of Caddy's
**active** admin configuration (the current admin endpoint is container-loopback,
so reading a host file cannot implement it); a complete runtime source with
approved historical component contracts/receipt provenance corroborated by actual
Engine resources, including unexpected managed applications; and source attestors
binding runtime/gateway/readiness payloads to exact port identity, host/daemon and
sampling round. HostContracts also needs independently approved IPAM/host/VPN,
quota/backup/restore/console/containment policy and a lease-aware recovery collector.
A separately reviewed bounded in-container admin reader or independent local
attestor is a possible Caddy transport; this task introduces neither arbitrary
container exec nor a fabricated empty active-config result. These are installation
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
binding pins, source policy A/B, protected paths and quiescent installation window.
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
