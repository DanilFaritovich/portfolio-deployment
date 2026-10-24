# C4.5 — Production host evidence and binding preparation

Assessment: 2026-10-24. **Installation BLOCKED; productionAuthorized=false.**
The Infrastructure v1.1.0 / Stage 08 completion is owner supplied. Analysis used
only two existing private local reports, never SSH/VDS, Docker, systemd or DNS.
Raw reports, host/daemon IDs, fingerprints, network addresses and host file hashes
are deliberately excluded from this handoff. Nothing here issues authority.

## Evidence levels and actual findings

- **VERIFIED**: a check with adequate evidence for its stated scope. Here that
  scope is local offline implementation/tests and a local byte comparison only;
  there is no independently verified production host attestation.
- **OBSERVED**: reported historical read-only facts, without independent attestation.
- **UNKNOWN**: insufficient evidence; never infer absence from denied/partial reads.
- **BLOCKED**: a mandatory prerequisite is unsatisfied. Unknown mandatory inputs
  leave installation blocked; they do not prove a defective host.

| Item | Status | Finding and limit |
| --- | --- | --- |
| OS / CPU / ABI | OBSERVED | Ubuntu 24.04.5 AMD64, 2 CPUs, Python 3.12.3, `cpython-312-x86_64-linux-gnu`, SQLite 3.45.1 |
| Python executable | UNKNOWN | `/usr/bin/python3` is a symlink resolving to `/usr/bin/python3.12`. The script hashes through the alias but stats the alias only; resolved executable metadata is absent. Pin the resolved regular file after independent review |
| Docker | OBSERVED | 29.9.0, `/usr/bin/docker`; administrative `sudo -n docker` reads succeeded; socket is recorded. This is not a current authenticated EngineContract |
| Compose | OBSERVED | 5.6.0 at `/usr/libexec/docker/cli-plugins/docker-compose`; three other fixed plugin files explicitly missing. The installed path is already supported by installer and SealedInstallation; no path fix is needed |
| gh | OBSERVED | 2.102.0 at `/usr/bin/gh`. Local comparison VERIFIED that recorded SHA256 equals the existing C4.4 official binary comparison pin; this does not verify the report's origin or the live host |
| ip / ss | OBSERVED | `/usr/bin/ip`, `/usr/bin/ss`; iproute2 package 6.1.0-1ubuntu6.4. Individual executable version output/provenance not established; retain C4.3 fixed paths |
| ssh-keygen | OBSERVED | `/usr/bin/ssh-keygen`; openssh-client package 1:9.6p1-3ubuntu13.19. Package version is not complete executable/dependency provenance |
| Tool file protection | OBSERVED / UNKNOWN | Docker, Compose, gh, ip, ss, ssh-keygen each have recorded root:root 0755 regular-file, one-link metadata and SHA256. Complete tool-parent protection, authenticated package origins and shared-library/stdlib/CA closure remain UNKNOWN |
| Engine inventory | OBSERVED | 0 containers, 0 volumes, 1 cached image; only default bridge/host/none networks, no members. Existing cached image must be independently classified, never deleted or inferred trusted |
| Network / ports | OBSERVED | No overlaps with proposed `172.30.240.0/24` and `172.30.241.0/24` in recorded Engine IPAM or recorded non-default IPv4/IPv6 routes; recorded TCP 80/443 have no listeners. Route defaults are not allocation conflicts. This is not complete host/VPN/network-namespace attestation or allocation approval |
| Firewall / public reachability | UNKNOWN | Owner reports UFW active with TCP 80/443 allowed. The preliminary inspection marks firewall unknown. Neither proves public DNS/TLS/reachability |
| Capacity | OBSERVED | Approximately 32.6 GiB available disk, over 4.9 million free inodes, 3.34 GiB available RAM, 2 GiB unused swap. These are counters, not reservations, quota enforcement or resource-limit approvals |
| Installation / storage / lock | OBSERVED | Fixed installation, unit, helper, bundle intake, operator trust, storage root, receipt and lock paths reported absent; existing destination ancestors recorded root-owned without group/other writes. No inode exists to bind yet. Recheck in the approved quiescent installation window; never manufacture a lock identity |
| HostPolicy / SourceTrust | BLOCKED | No independently approved phase hashes, authenticated prerequisite authority or fresh port/payload/round/machine/daemon-bound proofs |
| Approval / bootstrap / recovery | BLOCKED | Administrative binding, operator trust/custody and concrete bootstrap/public postcondition authorities are unconfigured; backup/restore, containment and console/equivalent evidence absent |

The preliminary `production-inspect` result remains **11 passed / 2 blocked /
5 unknown**. Engine/daemon failures in that access context are permission related;
the later administrative transcript records successful reads. This does not grant
`ansible` Docker access, invalidate its intentional restriction, or justify group/
sudoers changes. Neither report overrides the other's scope or becomes an attestation.

## Narrow offline tool-evidence check

Use the existing Makefile with an explicit **local** transcript path:

```sh
make production-host-evidence PYTHON=.venv/bin/python \
  HOST_EVIDENCE_REPORT=/absolute/private/c41-preflight.txt
```

The checker reads at most 2 MiB / 16,384 lines from a regular file, rejects final
symlinks/FIFOs and detects changed file metadata. It recognizes only the existing
C4.1 `stat → readlink → sha256sum` sequence for seven fixed tool paths and the four
fixed Compose plugin locations. Explicit absence, unsafe metadata, duplicate records
and plugin conflicts are distinguished from missing/incomplete evidence. gh version
must be exactly 2.102.0 to be reported OBSERVED; version presence never proves
Engine/Compose syntax capabilities or ABI/dependency closure.

Output is bounded allowlisted JSON: fixed paths, parsed version strings, evidence
statuses and fixed reason codes. No transcript text, hash inventory, IDs, addresses,
keys, input path or raw exception is emitted. It neither reads nor merges the
preliminary inspection JSON. It never runs a tool, writes a file or creates
HostPolicy, EngineContract, source proof, SealedInstallation or approval objects.
Even a complete synthetic transcript yields `installation=BLOCKED` and
`productionAuthorized=false`; exit 0 means analysis completed, not readiness.
Invalid-encoding/unreadable/oversized input exits 2 without echoing private data.
It is a diagnostic checker, not an authenticated transcript parser; injected text
cannot grant trust. The shipped administrative and production bindings still raise.

Offline validation: **27 targeted tests passed; make check passed, 2346 tests**.
Coverage includes unsafe ownership/modes/links, unresolved Python, wrong/ambiguous
gh version, duplicate/conflicting plugin records, incomplete hash/path sequences,
input bounds, redacted output and the Make wrapper. No real E2E was run.

## Fixed binding handoff for C4.6

No approved host policy or authority code was supplied, so no production authority
can safely be wired from these reports. Keep existing fixed paths and
`unix:///run/docker.sock`; keep proposed CIDRs and C4.1 capacity envelopes **unapproved**.
Use the existing installation/executor, not a parallel installer or engine.

| Owner / reviewer input | Concrete completion criterion |
| --- | --- |
| Administrator: supplementary host evidence | Separately authorize read-only collection of resolved Python metadata/hash and all tool ancestors; authenticate actual machine/daemon/socket, package and full library/stdlib/CA closure. Retain private values outside Git and compare freshness/completeness before approving any inventory |
| Owner: IPAM/storage/resource decision | Approve or reject the two proposed CIDRs, phase allocation, C4.1 disk/inode/RAM envelopes and actual budget/quota enforcement. Independently review complete kernel/Engine/host-network/VPN facts; new empty-host resources need no fabricated IDs |
| Owner: recovery and evidence authority | Name rebuild operator, replacement-VPS access/budget, encrypted off-host backup owner/retention/key custody and tested isolated restore including journal/ledger/LKG/certificates. Prove old-host isolation without SSH and tested console or explicitly approved equivalent. Bind evidence origin, host/policy, freshness and revocation in existing `prerequisites.verify`; rebuild selection alone satisfies none of these |
| Administrator: install decision | Review fixed independent admin authentication implementation and approve exact host, manager, helper/admin-binding, bundle and external-inventory tuple. No JSON/env/hash-only receipt can instantiate InitialInstallationApproval |
| Operator: signing trust | Select separate off-VDS Ed25519 signing identity, exact principal/public key, passphrase/custody/backup/rotation/revocation; independently approve existing operator-keys.json and ssh-keygen inventory pins. Do not reuse login key or create a key implicitly |
| Binding reviewer: first phase | Wire existing CandidatePreflight, PolicyHostContracts/LocalHostSources/ReadOnlyJournalSource, ReleaseVerifier, ProductionExecutor, ManagedBootstrapPort and SSHSIGVerifier to actual approved authorities. Bootstrap reader/attestor/resource pre/postconditions must verify real samples. Existing full composition.build requires post-bootstrap IDs; first-phase wiring must not invent them |
| Binding reviewer: phase transition | Review the sealed phase transition before installation: there is no automatic upgrade API. After bootstrap, independently reobserve actual network/container/image/socket identities before full SourceTrust/ACTIVE/readiness/public-postcondition composition; no in-place edits to pinned code |
| Maintainer / independent bundle reviewer | Separately authorize corrected protected-main integration/release, select its exact SHA and rerun source/CI/OCI/signature evidence. Rebuild C4.4 vendor/Caddy inputs with reviewed real binding, review Ubuntu 24.04 isolated imports and full external closure, compare repeated bundle bytes, then independently verify source/assets/whole ZIP |

The old C4.4 bundle selects historical main without the C4.3 `/usr/bin/ip` fix;
it remains unsuitable for installation. Python 3.12 vendor preparation exists;
its isolated imports were run on local glibc 2.43, not the target Ubuntu. No new
ABI defect is established by these reports; do not rebuild dependencies or relax
protection speculatively. gh installation and Compose path discovery are resolved
as historical observations, while independent current pins remain outstanding.

**Next minimal task, C4.6:** obtain the owner decisions and narrowly missing
read-only evidence above, then implement/review the fixed first-phase authorities
and sealed transition using existing ports. If these inputs are absent, leave the
bindings closed. Initial installation remains prohibited until independent bundle/
external-inventory review and exact administrative approval, with its own explicit
operation authorization. Image acquisition, unit start, first-bootstrap and first
ProjectRouter rollout each retain their separate approvals and fresh verifications.
No merge, release, installation, activation or rollout occurred in C4.5.
