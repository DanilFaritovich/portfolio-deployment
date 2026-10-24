# C4.1 — First production installation readiness

Assessment: 2026-10-24. **Blocked; no installation or operation authorized.**
This is a concrete handoff for the existing installer/executor. No VDS contact,
operator approval, key creation, systemd action, image acquisition or rollout was
performed. Rebuild on New VPS is the selected recovery strategy, without paid
Rescue Mode. It does not waive existing fail-closed requirements.

Current follow-up: [C4.5 host evidence and C4.6 binding handoff](production-host-evidence.md)
reviews the newer private reports. Historical missing-gh/plugin/host-capacity
statements below are superseded only as observations, never as production pins.

## Release and evidence provenance

| Input | Exact value / status |
| --- | --- |
| Manager protected-main selection | `02e22f9b3e05043517ba26c0d9637e8ae70de9b8`; current main independently read through GitHub API during this assessment |
| Caddy image | `ghcr.io/danilfaritovich/portfolio-caddy@sha256:6996e8f98c9a7876e73e362ac8b74990ba13915d97aad30b13af2be45e463d05` |
| Caddy publication | `40044410916/1`; API confirms successful push workflow `.github/workflows/caddy-image.yml` at the selected manager SHA; operator confirms exact-digest acceptance and both signatures |
| Validation Caddy | 2.11.7, linux/amd64; SHA256 `678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`; matches source lock and local `/tmp/trusted-caddy/prepared-2/context/caddy` bytes |
| ProjectRouter image | `ghcr.io/danilfaritovich/project-router@sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853` |
| ProjectRouter source / signer | `526c765f33932e163392dd1d512de4aa2f2a5260`, `.github/workflows/publish.yml`; admitted manifest anchors publication `39776962924/1` |
| Application origin / health | `https://danil-miftakhov.dev`, internal port 8080, `/en/`, linux/amd64, UID/GID 101:101; inspect raw manifest/policy at selected SHA |

API metadata is not full `ProtectedSource`, OCI or attestation verification.
Repeat those real verifiers immediately before installation/operations with their
independently reviewed authenticated ports. Do not reinterpret old reports as trust.
Local develop `6113c3d437a3876bd146dce1727c957dc4ae3a67` has the same tree as
selected main and is two history commits behind; it was not silently synchronized.

Operator-confirmed VDS baseline (not a fresh authenticated sample): Ubuntu 24.04,
`132.243.166.145:2222`, `portfolio_admin`, SSH key and working `sudo -n`; Docker
29.9.0, Compose 5.6.0; no containers/volumes/custom networks; bridge
`172.17.0.0/16`, public network `132.243.166.0/24`; HTTP/HTTPS ports free;
32.7 GiB free disk and 3.36 GiB available RAM; previously inspected installation
paths absent. `ansible` has no direct Docker socket access and keeps its current
permissions. These satisfy the reported OS/access/capacity outline, not installer
trust, complete network ownership or current absence checks.

Fresh read-only preflight must establish: host SSH key out of band, login key
fingerprint, machine ID, daemon ID, architecture/Python ABI, actual Docker root and
socket protection, exactly one Compose plugin and its real regular-file path,
Python/stdlib/shared libraries, gh exactly 2.102.0 and trust roots, ssh-keygen/ip/ss paths
and provenance, all-table IPv4/IPv6 routes/rules, addresses/VPN/host networking,
all listeners, full Engine inventory/cache/IPAM, protected ancestry/absence of
all destinations, mount/inode/free-space/quota facts, recovery owner/evidence.
Permission errors and missing tools are blockers; they never establish absence.
No historical Engine/Compose version alone proves current syntax compatibility.

## Independent administrative channel: minimal decision

Use the existing authenticated `portfolio_admin` SSH login on port 2222 and its
existing `sudo -n` authority, with strict host-key verification and a quiescent
installation window. A human administrator explicitly approves the exact tuple
below after independent workstation review. This is separate from both GitHub CI
and the later SSHSIG operation approval. No new SSH identity on the VDS, generic
sudo relay, root shell wrapper, sudoers change or `ansible` permission is needed.

**Proposed assertion for future administrator approval, not issued here:**
“I, the authenticated portfolio_admin administrator, approve initial installation
only on machine ID [pending], at 132.243.166.145:2222, of manager
02e22f9b3e05043517ba26c0d9637e8ae70de9b8, bundle SHA256 [pending], using helper
SHA256 [reviewed selection pending], admin binding SHA256 [pending] and exact
external-files inventory SHA256 [pending]. No executor start, image preparation,
Caddy bootstrap or application rollout is included.”

Pending fields must be replaced by independently verified exact values before
approval. An IP address is not a machine identity. Neither this text nor a
request JSON can instantiate `InitialInstallationApproval`.

Independently reviewed **fixed** `admin_bindings.py` must bind this human decision,
exact machine/bundle/source/dependency pins and authenticated intake; reject any
other bundle hash; then return existing `operator_bootstrap.InitialInstallationApproval`.
Keep review/verifier code outside the bundle. Do not implement approval as an
environment flag, arbitrary JSON receipt, always-success callback or hash-only
self-verification. Root-owned fixed code may embody the administrator's reviewed
one-install decision; its installation itself requires separate authorization.
No working administrative authority is supplied by the shipped raising binding.

Future authenticated administrative intake installs the separately pinned helper
and admin binding under `/usr/local/lib/portfolio-deployment-bootstrap`, stages
only verified bundle bytes at `/var/lib/portfolio-deployment-bootstrap/bundle.zip`
(root:root 0600, protected ancestry), and invokes only:

```sh
sudo -n /usr/bin/python3 -I -S -B /usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py
```

**Future mutation, do not run now.** Verify helper bytes/protection *before* first
execution. The invocation accepts no arguments. Installation creates only the
existing fixed bundle/storage/unit; it neither starts nor enables the unit.
Later signed request intake and exact unit recognition/start require separate
reviewed administrative actions; no reusable privileged command service is added.

## Fixed production wiring and authorities still required

| Dependency | Reuse / remaining concrete work |
| --- | --- |
| Engine/machine | `EngineContract`, `MachineIdentity`, `installed_engine`; fix canonical `unix:///run/docker.sock`, actual machine/daemon IDs and explicit read authorization |
| Host policy | `HostPolicy` + `PolicyHostContracts` + `LocalHostSources` + lease-aware `ReadOnlyJournalSource`; independently approve phase-specific network/resources/storage hashes, capacity/inodes and real allocation/budget/backup/console/containment evidence |
| External prerequisites authority | No production implementation selected. `prerequisites.verify(policy, identity)` must authenticate origin, exact host/policy, freshness and revocation; reference strings or a matching hash are insufficient |
| SourceTrust | Existing `RuntimeInventory`, `RuntimeSource`, `GatewaySource`, `ReadinessSource`, `LocalSourceAttestor`, `SourceBinding`, `SourceTrust`, `ProductionStateReader`; fixed composition binds exact port objects, payload, round and machine/daemon. Admit current/historical contracts and exact infrastructure identities; never load an inspection report as an attestation |
| Image evidence | Existing OCI mapper, `GHCRRead`, `HTTPSRead`, `GitHubRead`, `AttestationVerifier`, `AuthenticatedReleaseCollector`, `ReleaseVerifier`; authenticated source-read credential with readable enforcement for both repos, exact gh/root pins and immutable workflow/check/builder policy remain external inputs |
| Caddy provenance | Existing `CaddyReleaseCollector`; fixed Manager-repository GHCR/crypto ports, exact source/publication/input predicates, both signatures and exact published-image runtime acceptance. No replacement producer or provenance scheme |
| Bootstrap | Real fixed `bootstrap_reader.collect()`, `bootstrap_attestor.verify(sample, identity)`, `bootstrap_resources.preflight(prepared, runtime)` / `verify_created(prepared, runtime)` are still unconfigured production adapters. Reuse `BootstrapInput`, bounded Engine/kernel/storage reads, `BootstrapValidationBinding`, existing runtime comparator and ManagedBootstrapPort. Verify exact provenance before validation and actual created resources/ACTIVE empty config in two postcondition observations; never return the input unchanged as proof |
| ACTIVE gateway | Existing `installed_active_channel`, `UnixActiveChannel`, `PinnedCaddyRuntime`, `ActiveConfigTransport`, `ManagedGatewayReadPort`; actual container/proxy-network IDs, UDS inode/peer PID, image/config IDs and storage pins can only be approved after bootstrap |
| Public postconditions | Real fixed `gateway_postconditions.verify(routes, identity)` authority remains unconfigured; bounded public origin/DNS/TLS/path validation must corroborate actual active routes and host. Disk config or internal health alone is insufficient |
| Operator signature | Existing `SSHSIGVerifier`; separate Ed25519 signing key off VDS, exact principal/public key and `/etc/portfolio-deployment/operator-keys.json` hash. Decide passphrase, offline encrypted backup, authorized signer, rotation/revocation through protected inventory update; never reuse SSH login key or place private key on VDS |
| Fixed binding | Replace blocking `installation/bindings.py` only with independently reviewed sealed wiring over `composition.build`; no request/env-selected module. Supply all authorities above and existing `project_router_image_registry` factory |

`composition.build` currently expects post-bootstrap private/proxy/container IDs
and ACTIVE ports even for its initial wiring. Do not invent these IDs for an empty
host. A fixed bootstrap/cache binding can instantiate existing CandidatePreflight,
ProductionExecutor, release factory, PolicyHostContracts, ImagePreparation and
ManagedBootstrapPort directly with only that phase's actual authorities; compose
rollout wiring after independent verification of created IDs. This is trusted
wiring over the same engine, not a new installer/engine. Its exact code remains
pending host/policy inputs and independent review; sealed updates have no automatic
upgrade/migration API. Review the complete phase transition and inventories before
initial installation; do not edit pinned installed code in place as a shortcut.

External `InitialInstallationApproval.external_files` / `SealedInstallation.files`
must pin the resolved system interpreter, complete reviewed stdlib/system/native
library closure, `/usr/bin/docker`, exactly one actual regular Compose plugin,
real gh binary and trust-root files, `/usr/bin/ssh-keygen`, operator trust file,
`/usr/bin/ip`, `/usr/bin/ss`, both administrative helper files and all code/tools
used by fixed authorities. Independently confirm provenance and protected ancestry,
regular-file/one-link requirements, OS package origins and any shared-library/search
paths. Do not pin a symlink spelling or silently bypass protection. OS/package
updates invalidate pins and require reviewed maintenance. No current VDS file
hash, host ID, SSH fingerprint, operator public key or bundle hash is known here.

## C4.3 — Fixed host IP path and C4.2 evidence limits

The C4.2 read-only inspection, as supplied for this task, confirmed Ubuntu 24.04.5,
Python 3.12.3, Docker 29.9.0 and Compose 5.6.0, and found `/usr/sbin/ip` to be a
symlink to `/usr/bin/ip`. C4.3 changes the four fixed runtime route/rule commands
and the read-only diagnostic runbook to `/usr/bin/ip`. No new VDS inspection was
performed. The target's regular-file identity, ownership, one-link metadata,
protected ancestry, hash and package/dependency provenance still require
independent verification before production approval. Pinning the symlink spelling
is invalid; `SealedInstallation.protected()` remains strict.

The supplied C4.2 evidence establishes no further symlink incompatibility for
the other fixed runtime tools: `/usr/bin/docker`, `/usr/bin/ss`,
`/usr/bin/ssh-keygen` and installed `/opt/portfolio-deployment/tools/caddy`.
Their protected metadata/provenance and the one actual regular Compose plugin
remain unverified here; versions alone cannot establish these properties.
The interpreter already requires its resolved executable pin; C4.1's candidate
`/usr/bin/python3` is not evidence of a valid regular-file inventory entry.
No fallback search, configurable tool path or protection exception is added.

Missing gh exactly 2.102.0, a reviewed Python 3.12 target-ABI vendor tree,
an independently verified bundle, trusted administrative/source/bootstrap/public
postcondition authorities and recovery prerequisites remain explicit blockers.
This path correction grants no installation, binding, operator approval, image
acquisition, systemd action or production operation authority.

## Real packaging defect and narrow fix

At selected main, `caddy_release.py` imports `scripts.caddy_producer`, omitted by
`source_target`; producer `load_lock()` also requires the omitted source files
`images/caddy/{inputs.lock.json,Dockerfile,github-trusted-root.jsonl}`. An exported
bundle therefore cannot use this real Caddy verifier, irrespective of host approval.

The C4.1 fix exports the existing inert producer module and exactly those three
source assets, permits only these exact additional paths in the administrative
parser, and lets the existing independent tree verifier check their Git identities.
No downloads run on import, no producer CLI is installed as an executable or
invoked by the installer, no new trust source or permissive binding is introduced.
An isolated subprocess regression imports the actual exported verifier, evaluates
its lock/predicate without the checkout, and fails when the lock is removed.
Unknown image assets remain rejected. This closure test uses dev dependencies;
it is not a target-ABI vendor or production acceptance test.

The corrected exporter/reviewer/helper are separately reviewed tooling outside the
candidate bundle. They can export the **unchanged selected main source**, where
all four missing inputs already exist, once independently accepted/pinned. Do not
use the old main helper for that corrected bundle: its parser refuses these paths.
Alternatively use a separately approved future protected-main tooling release.
This task does not merge/release either option or promote develop into production.

## Reproducible bundle procedure and missing inputs

A production-ready bundle cannot be assembled/independently approved now: actual
Ubuntu Python ABI and its dependency closure, verified wheel hashes/origins,
working reviewed fixed binding and external authorities/pins are missing. Local
Python is 3.14.4; copying `.venv` would produce the wrong native ABI and omit
`typing-extensions`, required by referencing on Python <3.13. No fake bundle/hash
or synthetic dependency bytes are presented as installation artifacts.

On a reviewed unprivileged Ubuntu 24.04 linux/amd64 builder matching the freshly
observed Python ABI, prepare outside the checkout:

1. Independently authenticate wheels and record a complete hash-locked runtime
   requirements file. Current selected versions: PyYAML 6.0.3, jsonschema 4.26.0,
   attrs 26.1.0, jsonschema-specifications 2025.9.1, referencing 0.37.0, rpds-py
   2026.9.1; select/review exact typing-extensions version for Python <3.13.
   Include all actual marker/transitive dependencies; dev pytest is unnecessary.
   No source build, mutable resolution, inherited site-packages or .pth hooks.
2. Export from a verified wheelhouse, with independently obtained wheel hashes:

   ```sh
   python3 -m pip install --no-index --only-binary=:all: --no-compile --require-hashes \
     --find-links /absolute/reviewed/wheelhouse --target /absolute/new/vendor \
     -r /absolute/reviewed/runtime-requirements.lock
   ```

   Review every exported file/native module/license and import closure with target
   interpreter `-I -S -B`, adding only vendor/source explicitly. Remove no required
   package data. Reject symlinks/bytecode/.pth/unsafe names. Hash final bytes into
   `runtime-files.json`: exact `vendor/<relative-file>` and `tools/caddy` keys.
   A generated inventory is comparison input, not independent provenance.
3. Authenticate official Caddy release archive/certificate/signature using the
   source input lock and existing `upstream_command` policy, including source SHA
   `72dd0fb067f6d7826c7f79907670ba4a713bfe37`. Archive SHA256 is
   `727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b`.
   Verify static linux/amd64 binary pin from the table above. Existing matching
   local bytes do not replace independent publisher verification.
4. From separately reviewed corrected tooling, with absent output file:

   ```sh
   .venv/bin/python -m scripts.production_integration.install_bundle \
     --revision 02e22f9b3e05043517ba26c0d9637e8ae70de9b8 \
     --vendor /absolute/reviewed/vendor --caddy /absolute/reviewed/caddy \
     --dependency-pins /absolute/reviewed/runtime-files.json \
     --bindings /absolute/reviewed/bindings.py --output /absolute/new/bundle.zip
   sha256sum /absolute/new/bundle.zip
   ```

   The Make wrapper has no bindings override and deliberately exports the raising
   binding. Use it only to inspect the blocking default. Build twice to different
   absent paths with identical inputs and `cmp` the ZIP bytes; deterministic v1
   ZIP has fixed metadata and no compression. Bounds remain 128MiB/64MiB/4096.
5. On an independent reviewed workstation, instantiate `ManagerBundleVerifier`
   with fresh authenticated `GitHubRead`, Manager `WorkflowCheck('DanilFaritovich/portfolio-deployment',
   'Offline checks', 15368, '.github/workflows/offline.yml')`, independently
   established complete asset pins and replacement `bindings_pin`; call
   `verify(bundle_bytes)`. Policy B still requires effective protected-main and
   exact-SHA successful push CI before/after full tree verification. Check every
   source blob and asset inventory/hash, no extras, and whole-bundle hash against
   builder output. Do not import verifier code from the bundle under review.
6. Independently review the resolved external inventory and helper/admin binding
   before the authenticated administrator approves their exact tuple. Preserve
   reviewed source tree, wheels/verification receipts, per-file inventory, whole
   ZIP hash and review evidence off VDS. Review result alone grants no privilege.

`artifacts/c4.1/source-pins.sha256` contains reproducible **local comparison pins**
for selected release inputs and the corrected helper/tooling. `source-pins.json`
records provenance/categories. Neither file is external production approval.
The C4.3 refresh covers changed local source/runbook/documentation bytes only;
the historical `manager_sha` and `release_source_files` still describe the C4.1
selected release, which predates the path fix. Build and independently review a
new exact protected-main selection before production; these comparison hashes
cannot approve that selection or the host's `/usr/bin/ip`.
Known blocking-binding hashes are included to detect accidental substitution,
not to endorse them as functional wiring. Complete vendor/external/bundle hashes
remain explicitly unavailable until real reviewed inputs exist.

## Proposed IPAM, paths and resource envelopes — NOT APPROVED

Propose proxy `172.30.240.0/24`, gateway `172.30.240.1`, and application private
`172.30.241.0/24` (expected automatic gateway `172.30.241.1`, to be observed). These do not overlap the two reported
networks, but complete host/VPN/all-table/Engine IPAM evidence is still absent.
Do not create/approve either CIDR until fresh conflict review. No resource IDs
exist yet. Keep established resource names; expose only gateway TCP 80/443,
keep UDS admin local and app 8080 internal. No DNS/firewall change is included.

Keep installer paths: `/opt/portfolio-deployment`; the fixed unit path;
`/srv/portfolio/{locks,state,journal,requests,caddy-config,caddy-lkg,certificates,caddy-data,caddy-admin,temporary}`.
Directories root:root 0700; lock and private files 0600; Caddy executable 0700;
lock inode never replaced. `journal/journal.sqlite3` remains the single journal
and ledger. Certificates and Caddy data are persistent; admin socket is root:root
0600 under the existing UDS contract. No alternative storage root is proposed.

Preliminary planning ceilings within reported free capacity: 1 GiB installation/
staging, 4 GiB Docker image cache/layers, 1 GiB bounded logs, 1 GiB operational
reserve (plus explicit image headroom); retain remaining space unallocated.
Reuse existing small storage review budgets: state 64MiB, journal 16MiB per-file
bound with 32MiB transaction reserve, config/LKG 8MiB each, certificates 256MiB,
Caddy config data 32MiB, temporary 32MiB; requests <=128KiB per request, envelope
<=64KiB launcher bound (SSHSIG verifier bound is tighter). Include lock/admin
metadata and actual filesystem/inode overhead. These are ceilings for review,
not quotas, allocation proofs or permission to retain unbounded content. The
HostPolicy 32MiB bounded content traversal is separate; a 256MiB certificate
budget does not permit observations beyond that parser bound.

RAM planning only: reserve 1GiB for OS/Docker/SSH, 512MiB for bounded one-shot
manager, 256MiB Caddy and 128MiB ProjectRouter; leave remaining reported capacity
as headroom. Runtime rendering has no approved CPU/memory quota enforcement;
these numbers are not installed limits, measured peaks or a reason to alter image
contracts. Verify CPU count/cgroups/quota mechanism and budget enforcement owner
before claiming storage/resource compliance. Propose minimum free disk 1GiB and
100,000 free inodes only after inode counts and backing filesystems are checked.

## Recovery: Rebuild on New VPS

A rebuild may replace a lost host; it does not restore journals, consumed approvals,
LKG, certificates, trust inventories or data. No paid Rescue Mode is required by
this plan, but no free console or recovery capability is assumed to exist.

Still unfulfilled: an authenticated independent prerequisites authority; encrypted
off-host backup with ownership/access/key custody and retention; isolated restore
drill of actual installer/journal/ledger/config/certificate state; provider console
or independently approved equivalent with a tested owner/access drill; and exact
first-release containment when no prior application release exists. A local empty
filesystem or stateless ProjectRouter does not waive manager/ACME recovery.

Before activation, name the rebuild operator and replacement-VPS budget/access;
prove provider ability to stop/isolate a compromised old VPS without SSH, recover
credentials/backups, provision a fresh hardened host through Infrastructure,
verify new machine/daemon/host keys and independently reapprove all host-specific
policies. Restore only reviewed compatible data and preserve unresolved ledger/
journal evidence; old host approvals are not transferable and consumed candidates
cannot be replayed. Define later separately authorized DNS cutover and prevent
simultaneous serving/ACME writers. Test expected RPO/RTO and document acceptance.
The current console/backup/containment contract must be satisfied or explicitly
changed through its own reviewed scope; selecting rebuild alone satisfies none
of those gates. No auto reset/retry, destructive cleanup or pin bypass is offered.

## Next step and separate operation boundaries

Run the adjacent [read-only operator runbook](../artifacts/c4.1/read-only-preflight.sh)
only after independently confirming SSH host/login pins; it records diagnostics,
not `SourceTrust`/HostPolicy proof. It deliberately does not execute this checkout
as root. Review its output before selecting pins; transient or denied reads block.

1. Now: administrator obtains fresh read-only facts and recovery/provider evidence;
   approves proposed channel/IPAM/budgets and selects keys/authorities. Review the
   narrow bundle-closure fix and the concrete fixed binding code/dependency closure.
2. **Initial installation, separate permission:** independently verified bundle,
   helper/admin binding, full external pins and exact human installation decision;
   protected intake and fixed helper invocation only. Inspect preserved lock/files
   and installation verification afterward; no start/enable or Docker activity.
3. **Image preparation, separate permissions:** ProjectRouter existing
   `prepare-project-router-image` candidate/SSHSIG/ledger scope; Caddy acquisition
   requires its separate reviewed exact-digest administrative binding/provenance/
   acceptance, because ImagePreparation intentionally only permits ProjectRouter.
   No broadened pull scope is implemented here.
4. **Caddy bootstrap, separate permission:** actual phase policy/authorities,
   cache/provenance, fresh signed `first-bootstrap`; existing executor creates
   approved resources, validates empty config, starts pinned Caddy, verifies twice
   and records bootstrap LKG. Unit recognition/start and signed intake need their
   own explicit administrative authorization. Preserve evidence on any ambiguity.
5. **ProjectRouter rollout, separate permission:** independently reobserve actual
   network/container/socket IDs and policy hashes; reviewed sealed phase wiring;
   fresh `first-project-router-rollout` signature. Internal readiness, full ACTIVE
   verification and real public DNS/TLS/path checks gate application LKG. DNS,
   firewall or provider changes require their own authorization if needed.

Minimal blockers: (1) fresh host/tool pins + reviewed admin decision; (2) compatible
verified vendor and independently reviewed corrected bundle/helper; (3) concrete
phase bindings/authorities and operator-key custody; (4) enforced host/IPAM/storage
policy plus proven backup/restore/console/containment. Public DNS/TLS and actual
post-bootstrap IDs additionally block first rollout. No additional deployment
engine, generic installer, scheduler or framework is necessary.
