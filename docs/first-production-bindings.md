# C4.6 — First production bindings and owner handoff

**Installation BLOCKED; no production operation authorized or performed.**
This is an offline-tested integration, not host attestation or an issued decision.
The [C4.7 installation runbook](first-production-installation-runbook.md) now supplies
concrete opt-in administrative consumers and a finite sealed phase transition;
the owner inputs and production bindings below remain unapproved/closed.
Private C4.1/C4.5 reports remain private historical observations; do not import them
as policy, SourceTrust or administrative approval. Infrastructure v1.1.0 and tool
versions remain owner-reported/observed at the scopes in the
[host evidence handoff](production-host-evidence.md).

## Implemented first phase

`production_integration.first_phase.build_first_phase` is a fixed Python recipe
for reviewed installed bindings, not a configuration loader. It composes the same
ProductionExecutor, CandidatePreflight, EngineContract/MachineIdentity on
`unix:///run/docker.sock`, PolicyHostContracts, LocalHostSources,
ReadOnlyJournalSource and SSHSIGVerifier. There are no rollout/provider/probe/
gateway-write/image-preparation ports in this phase. Requests cannot choose ports,
modules, socket, directories or adapters. Constructors do not contact the Engine.
The standard `installation/bindings.py` still raises; the recipe is not enabled.

`EmptyHostReader` authenticates only its exact fresh in-process sample against
independently approved HostPolicy and prerequisite authority. It repeats bounded
Engine/storage reads, refuses containers/volumes or existing gateway data, and
binds exact machine/daemon and phase hashes. Historical cached images are not
adopted, deleted or certified by this reader. The existing planner checks IPAM and
listener conflicts; a reviewed allocation authority must cover kernel/VPN scope.
Installed empty directories and lock/journal are allowed; runtime IDs are not needed.

`BootstrapResources` checks the exact prepared baseline/runtime/plan and invokes
existing CaddyReleaseCollector with protected-source policy B, OCI verification,
SLSA and signed producer-input provenance v2. An independently installed
`runtime_acceptance.verify(runtime, evidence, identity)` must return that same
artifact evidence after authenticating exact-image acceptance; signatures alone
cannot satisfy it. Cached Engine image bytes must match. BootstrapValidationBinding
adds official empty-config validation; ManagedBootstrapPort performs the existing
scoped actions only after the executor's separate signed approval and durable ledger.

Postconditions observe new proxy/private-network/container IDs; validate ownership,
complete IPAM/membership, unchanged foreign resources, actual storage/capacity and
empty Caddyfile; then reuse PinnedCaddyRuntime, UnixActiveChannel and
ActiveConfigTransport for image/security/mount/UDS/peer-PID and empty ACTIVE checks.
Two stable rounds (four ACTIVE GETs) within 15 seconds yield existing readiness
evidence. Failure yields no successful bootstrap LKG and performs no cleanup.
These in-process IDs are not approval for a rollout composition.

## Administrative contract, still closed

`installation/admin_bindings.py` adds typed InitialInstallationDecision and
`authenticate_decision`, a stdlib-only adapter to the existing standalone helper.
It validates machine ID (32 lowercase hex), protected Manager SHA (40 hex), full
ZIP/helper/admin-binding SHA256 (64 hex), and sorted external inventory. Inventory
hash is SHA256 of compact sorted-key ASCII JSON of the sorted `[absolute_path,
sha256_hex]` array, without newline. Helper/binding hashes must occur in that exact
inventory. It also requires decision ID (32 hex), `not_before`/`expires` epoch
seconds with at most one hour validity, and the presented whole-bundle hash.

The mandatory separately installed `authority.consume(decision)` must authenticate
administrator/channel, protected-main and independent whole-bundle review, actual
machine/intake/external inventory, validity/revocation and atomic one-time use;
it returns the exact decision object, never a boolean. This contract does not
implement or select that authority. No receipt/JSON/env loader issues an approval.
The shipped `authenticate_initial_installation` still raises. The existing helper
independently rechecks actual machine, bytes, destinations and protection before
writing, and never starts/enables the executor. Pin/authenticate helper and binding
before first execution; self hashing after execution is insufficient.

## Short owner decision sheet — proposals, NOT approved policy

Keep private values and signed evidence outside Git. Owner/reviewer must fill each
selected choice and exact fields; absence keeps the corresponding gate closed.

| Decision | Proposed choice and concrete consequence / alternative |
| --- | --- |
| IPAM | Retain proxy `172.30.240.0/24`, gateway `.1`; private `172.30.241.0/24`, expected gateway `.1`. Approve `network_allocation` evidence reference plus phase `network_hash`/`resources_hash`, after fresh complete conflict review. Alternative: supply two nonoverlapping private IPv4 CIDRs and proxy gateway; regenerate the exact candidate/policy. No preexisting IDs for new resources |
| Paths/storage | Keep fixed installer paths and root:root 0700 directories, 0600 files/lock/UDS. Approve `storage_hash`, `storage_budget` evidence reference, `minimum_bytes` (propose 1 GiB), `minimum_inodes` (propose 100,000), certificate budget (propose 256 MiB) and named enforcement owner/mechanism. C4.1 per-directory budgets remain proposals. Alternative quota partition needs independent host review; no silent path change |
| CPU/RAM/disk | Review C4.1 planning envelopes: OS 1 GiB RAM, Manager 512 MiB, Caddy 256 MiB, ProjectRouter 128 MiB; staging 1 GiB, images 4 GiB, logs 1 GiB, reserve 1 GiB. Select budget-only supervision with named operator and evidence, or actual approved limits/quota mechanism. Current renderers do not enforce these planning numbers; choosing enforced limits requires scoped contract changes before activation |
| Recovery | Confirm rebuild operator and replacement-VPS access/budget; off-host encrypted destination, retention, backup owner, key custodian; isolated restore evidence covering journal/ledger/LKG/certificates; old-host isolation method without SSH; tested console or approved equivalent. Supply `backup_restore`, `console_recovery`, `containment` authenticated evidence references. Rebuild preference alone satisfies none of them |
| Independent authority | Name verifier implementation/reviewer, authentication origin/trust roots, exact host/daemon and `HostPolicy.identity`, evidence issue/expiry/revocation mechanism and the five references above. Choose an independently reviewed authenticated service/channel or separately reviewed fixed administrative authority. A local unsigned report/hash cannot implement `prerequisites.verify`. Separately identify exact Caddy acceptance digest/source/run/attempt, issuer and revocation/freshness policy |
| Operator key | Choose separate off-VDS Ed25519 identity (recommended) or an explicitly reviewed existing dedicated signing identity. Supply exact principal/public key for `/etc/portfolio-deployment/operator-keys.json`, its approved SHA256, ssh-keygen pin/closure, passphrase/custodian, backup/rotation/revocation owner. No key is created here; login keys are not implicitly reused |
| Initial installation | Supply exact machine ID, protected Manager SHA, full ZIP SHA256, helper SHA256, admin-binding SHA256, sorted external inventory/hash, decision ID, validity window and authenticated one-use decision issuer. Scope: helper/intake plus installation only, no unit start/image acquisition/bootstrap/rollout. Defer this decision until independent artifact review; this document is not approval |
| Release/bundle | Separately authorize protected-main integration/release after review; select exact SHA with successful required checks. Independent reviewer supplies complete vendor/Caddy/external Python 3.12 Ubuntu 24.04 stdlib/library/CA/tool inventory, repeated ZIP byte comparison and ManagerBundleVerifier result. Reject historical C4.4 ZIP. A develop bundle or CI result grants no install authority |

The storage hash includes actual device/inode metadata: obtain it after installer
provisioning, never predict an inode. Before approving the installation artifact,
review how the external authority supplies this post-provisioning phase policy
without editing sealed code/inventory. This recipe does not add a policy-file
loader, authority issuer or update API. The same constraint applies after bootstrap:
review fresh actual IDs, next-phase hashes and full SourceTrust/ACTIVE/public-route
postconditions through a separately sealed binding transition, never in-place edits.

## Only missing read-only evidence

No new host probe was executed. Do not rerun full C4.1/C4.5 diagnostics. On separate
read-only authorization, collect only resolved Python regular-file metadata/hash
and missing protected ancestors (bounded fixed paths), plus independently sourced
package/stdlib/shared-library/CA provenance closure and backing mount/quota evidence
needed by the selected enforcement policy. Do not run unreviewed binaries to infer
their own provenance. A minimal supplemental Python metadata probe is:

```sh
timeout 3s /usr/bin/readlink -e /usr/bin/python3
timeout 3s /usr/bin/stat -c '%F %u %g %a %h %d %i' / /usr /usr/bin /usr/bin/python3.12
timeout 3s /usr/bin/sha256sum /usr/bin/python3.12
```

Fixed paths only; failed/truncated/permission-denied output is UNKNOWN. Keep output
private with collection time and authenticated origin. These commands do not
establish dependency closure or independent authority. If resolved path differs,
stop and review it rather than substituting a path automatically. Current identity,
phase hashes and absence/conflict facts must still be freshly checked in their
approved installation/operation window using existing ports, not another report parser.

## Remaining path to the first installation

Owner chooses fields above → independent authority and fixed binding review →
separately authorized protected-main release and new reproducible bundle → independent
source/ZIP/external-closure review → exact one-use administrative decision → separately
authorized installation. No SSH, Docker mutation, key creation, systemd action,
release, merge, Caddy bootstrap or ProjectRouter rollout occurred here. Unit start,
image acquisition, Caddy bootstrap and rollout retain separate owner authorizations.
