# Stage 09.4A — Production deployment preparation

Status: implemented preparation interfaces and offline acceptance. **Production
apply is unavailable.** Stage 09.4B is the separate first authorized rollout;
09.5 update/rollback acceptance and 09.6 automatic CD remain future work.

[Stage 09.4B.4 installation/integration](production-installation.md) adds exact
bundle review, separate administrative installation, bounded local sources and
cache-only image preparation using the existing executor/release gates. Deployment
owns manager installation/storage/Caddy/networks; Server Infrastructure owns the
prepared host. Independent authentication/attestors, GitHub policy A/B and Caddy
provenance remain blockers; no production operation was performed.

## Architecture and implemented interfaces

The historical preparation-only boundaries below are extended by
[Stage 09.4B.3 execution](production-execution.md): one-shot coordination, durable
authorization consumption and fixed mutation ports now exist and are tested offline.
Production installation, trusted bindings and live operations remain unconfigured.

[ADR 0001](adr/0001-production-execution.md) selects a host-local one-shot
controller/executor behind an independently provisioned privileged boundary.
Stages 03–07 retain the planner, Compose renderer, resource preflight, executor,
readiness, journal/LKG and recovery; there is no second deployment engine.

| Component | Preparation behavior / boundary |
| --- | --- |
| `compose_version.supported_compose` | Stable plugin versions >= 2, including future major/minor/patch versions and Ubuntu build suffixes; optional `v`; rejects v1, prereleases and malformed versions. Required CLI options are checked independently through plugin help; each actual artifact must pass `config -q` before `up` |
| `docker.ProductionDockerAdapter` | Fixed local Engine endpoint, machine/daemon checks, exact inventory/inspect argv and bounded deadlines/output; never sudo; all writes blocked |
| `trust.SourceTrust` | Expected host, daemon and per-source identities plus installed authenticated attestor over exact source payload/round; no trust from source booleans |
| `observations.ProductionStateReader` | Existing complete-source/Engine corroboration through CompositeStateProvider; now fails before promotion if source trust is unconfigured or mismatched |
| `http_read.InternalHTTPRead` | Numeric IPv4 in reviewed private subnet, admitted port/path, absolute deadline, 4KiB status-line cap; no DNS, proxy, redirects, response body or raw diagnostics |
| `gateway_read.ManagedGatewayReadPort` | Independently attested active gateway; supports exact empty managed config; foreign/unavailable Caddy is never treated as empty |
| `bootstrap.plan_bootstrap` | Authenticated empty-host or exact-existing-proxy proposal; IPAM/resource/listener/storage conflicts block; no operation executor |
| `locking.production_host_lock` | Existing flock primitive with one canonical path, existing metadata/parent checks and real lease/inode validation; no provisioning/fallback |
| `storage` | Explicit bounded budgets and read-only existing-directory metadata checks; not resource ownership or backup evidence |
| `release.ReleaseVerifier` | Exact candidate checks through the Stage 09.4B.2 read-only authenticated collector; default remains unconfigured; existing OCI byte verifier handles index → manifest → config |
| `authorization.review_approval` | Validate authenticated operator claims against every candidate field and a maximum one-hour window; returns review claims, no executable capability |
| `caddy_image.inspect_caddy_contract` | Reject incompatible image defaults and projection; always retains runtime/provenance/official-validation blockers |

Attestors, release collectors and approval verifiers are trusted installation
code, never objects constructed from fixture/manifest JSON. No live collector,
active-config attestor, operator signature verifier or privileged executor is
configured by CLI/CI. Missing implementations block. SourceTrust checks a typed
attestor result bound to exact data; it cannot cryptographically authenticate a
channel on its own. These ports deliberately expose the missing trust work.

The Docker endpoint policy accepts only the standard local `/run/docker.sock`
and its explicit `/var/run/docker.sock` spelling, executes against `/run` and
verifies that canonical socket. This avoids rejecting Ubuntu's normal `/var/run`
symlink while refusing arbitrary endpoint aliases. `docker-permission-denied`
means insufficient access, never absent Docker. SSH inspection remains a
separate inventory/known_hosts mechanism; no dynamic sudo or SSH command is added.

## Operator observations, not trusted live state

The operator reported on 2026-10-22: Ubuntu 24.04.5 x86_64, Engine 29.9.0,
Compose 5.6.0, SSH 2222/user `ansible`, no direct socket access and successful
administrative `sudo -n docker info`. UFW is active with 22/2222 and 80/443
allowed; no containers, volumes or nonstandard networks, no 80/443 listeners.
Reported capacity: 3.8GiB RAM (about 3.3GiB available), 2GiB swap, 38GB disk
(about 33GB available). These observations can be stale and authorize nothing.

The earlier diagnostic summary was 9 passed / 2 blocked / 6 unknown. Permission
denied does not mean Engine is missing. Compose's old normalizer explains its
unknown result; a missing host Caddy binary is expected for a container gateway.
Unprivileged UFW visibility is not proof of firewall failure. Security updates
remain a separately authorized infrastructure task. No repeat VDS contact was
made by Codex, and these facts were not converted into a planner snapshot.

## First-host bootstrap versus update

1. Operator establishes fresh authenticated host/daemon, complete Docker,
   listeners, network/IPAM, gateway and storage observations under the reviewed
   identity. Unknown scope blocks. Absence is not creation permission.
2. Review proxy/private subnets against every Docker subnet and host/VPN routes;
   review the candidate's runtime/storage hashes. The offline planner checks
   supplied complete Docker IPAM; host/VPN routing evidence remains an additional
   external prerequisite.
3. Obtain a separate `first-bootstrap` approval. The proposal creates only
   manager-owned proxy/private networks, private journal/storage, and an officially
   validated empty managed Caddy configuration/runtime. No application route is
   part of bootstrap authority. No invented active routes or new network IDs.
4. Future execution must hold the canonical lock, reobserve absence/conflicts,
   persist intent before each mutation and attest returned resource IDs and exact
   ownership/configuration after creation. Never adopt by name or label alone.
   Existing proxy reuse requires its approved exact ID, labels and full IPAM.
   Foreign resources, occupied ports, foreign/unknown Caddy or storage, overlapping
   IPAM and an expected network that disappeared block without replacement.
5. Obtain a separate exact-candidate `first-project-router-rollout` approval.
   Deploy application without its public route; verify bounded container and
   internal `/en/` readiness; officially validate the complete retained candidate;
   preserve the empty gateway baseline atomically; activate the route; verify
   public behavior before promoting the first application/gateway LKG.

BootstrapProposal contains a manager-only operation list and a distinct release
sequence; both are review data, always blocked for real execution in 09.4A.
An existing deployment uses the normal Stage 03 reconciliation and Stage 06
lifecycle rather than this first-host plan. Image-only updates preserve Caddy's
configuration/process and unrelated retained routes.

First installation has **no previous application LKG**. A failed initial readiness
check leaves the public route absent; it cannot roll back to a fictional release.
Containment/removal of only proven newly created ephemeral resources needs its
own reviewed action; certificates, journal, diagnostics and persistent data stay.
If activation outcome is unknown, observe/analyze under the lock, retain original
intent/LKG and prohibit replay. A previous empty Caddy config is a gateway recovery
baseline, not application rollback. Journal/LKG/fsync failures block success.

## Canonical lock and durable storage contract

`RuntimePaths.lock` is the directory `/srv/portfolio/locks`; the sole production
lock file is `/srv/portfolio/locks/host.lock`, for the entire local Docker Engine.
Every future bootstrap/application/gateway/recovery/journal operation must acquire
`production_host_lock` and pass the same live lease. Workstation locks and
per-application or per-daemon alternate paths cannot substitute for it.

Provisioning must create the lock directory as execution UID-owned 0700 and the
file as 0600, regular, one link; all ancestors must be directories owned by root
or execution UID with no group/other writes or symlinks. Runtime checks reject
mismatch and missing paths. Release closes the fd without unlinking; competing
flocks fail busy. Journal now requires an actual lease issued by `host_lock`,
matching the open inode, rather than a manually constructed `active=True` value.
ProductionGateway's default verifier additionally requires the canonical path.

Stage 05/07 disposable tests keep their injectable `/tmp` test locks. They are not
production controllers. Migration, if any old controller exists: disable all
writers; inventory old leases/inodes and incomplete journal runs; acquire/review
the old locks while quiescent; independently provision the canonical inode; update
all launchers together; verify mutual exclusion before enabling any writer. Never
unlink a held lock or silently migrate/fallback. No deployed controller/migration
was inferred from the operator's empty host report.

Proposed sibling paths (operator review/provisioning still required):

| Purpose | Path | Operator budget / durability |
| --- | --- | --- |
| State / immutable plans and receipts | `/srv/portfolio/state` | 64MiB; private files 0600, directory 0700 |
| SQLite v1 journal / application LKG | `/srv/portfolio/journal/journal.sqlite3` | 16MiB existing per-file bound, reserve 32MiB for DB/transient journal; DELETE + synchronous FULL |
| Caddy active/candidate config | `/srv/portfolio/caddy-config` | 8MiB; private atomic temporary write, fsync, rename, directory fsync |
| Caddy LKG | `/srv/portfolio/caddy-lkg` | 8MiB; immutable previous full config + hash, durable before activation |
| Certificates / ACME data | `/srv/portfolio/certificates` → `/data` | 256MiB initial review budget; private, persistent, encrypted off-host backup |
| Caddy autosave/config state | `/srv/portfolio/caddy-data` → `/config` | 32MiB; private persistent storage |
| Temporary staging | `/srv/portfolio/temporary` | 32MiB, private, same-filesystem atomic config staging where required; no credentials in shared `/tmp` |
| Free space reserve | Host filesystem | At least 1GiB plus separately budgeted Docker image/layer storage |

StorageBudgets validates bounds/total, not live quotas. Operator must enforce and
monitor budgets, disk/inode availability and retention without deleting unfinished
receipts or LKG. All storage is execution-owned; Caddy `/data` and `/config` access
must be proved for the selected image's effective UID. Config mounts set
`create_host_path: false` so Compose cannot silently create missing bind sources.
No directory, quota or backup was created on the VDS.

Back up SQLite with its backup API under the canonical lock or with all writers
quiesced; never copy a live DB while ignoring its transaction file. Keep hashed
manager/manifest/policy/Compose/evidence, Caddy full LKG and protected certificate
storage in an encrypted off-host backup with access/retention owner. Restore into
an isolated directory, check schema/integrity and ownership, compare fresh host
state and analyze unfinished intent before any new approval. A backup is not
verified until an isolated restore drill succeeds. No automatic reset/resume,
volume removal, pruning or destructive data restore is supplied.

## Caddy image, runtime and TLS

The [official Dockerfile template](https://github.com/caddyserver/caddy-docker/blob/master/Dockerfile.tmpl)
and [current Alpine Dockerfile](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile)
were reviewed as source on 2026-10-22. They define a full `caddy run` CMD, no
ENTRYPOINT/USER override, `/srv` working directory, XDG data/config at `/data` and
`/config`, and binary `cap_net_bind_service`. These mutable source links are not
proof about any selected digest. EXPOSE declarations do not publish host ports.

Manager projection keeps the full command, only TCP 80/443 publishes, container
loopback admin 127.0.0.1:2019, read-only root/config, persistent `/data` and `/config`,
capability drop plus NET_BIND_SERVICE and no-new-privileges. No Docker socket,
public admin or application host port. Empty bootstrap config has only the admin
global block, no synthetic hostname and no route/certificate request.

Before 09.4B choose a trusted immutable Caddy digest, verified linux/amd64 config
and exact version. `inspect_caddy_contract` rejects incompatible entrypoint/CMD,
user, working directory or XDG/version defaults, but **always retains isolated
runtime, provenance and official-validation blockers**. Verify effective startup,
root/file capabilities under no-new-privileges, writable paths/permissions,
restart and storage persistence using that exact image and the selected Compose plugin in an
isolated environment. No Caddy runtime test or production pull was performed here.

TLS prerequisites: independently verify authoritative A/AAAA (remove or repair
unreachable AAAA through a separate infrastructure task), domain control, CAA,
ACME directory/terms/account policy, outbound resolver/HTTPS connectivity, clock,
public 80/443 reachability and storage capacity/backup. Validate the full config
with the exact trusted official Caddy binary/image before activation. Establish
operator-approved staging-CA tests separately from production certificate storage;
never substitute test certificates as production acceptance. First real issuance
requires explicit 09.4B permission. Check external HTTPS certificate/hostname,
redirects and `/en/`, then persist LKG. Reload/post-check failure retains the
previous full config; uncertain outcome requires fresh active-config inspection,
not blind replay. Filesystem config/LKG alone cannot attest active Caddy state.

## Release evidence and ProjectRouter handoff

Read-only GitHub metadata on 2026-10-22 reported producer `main`
`c849326f507e0319b0c28ac3542e9a894a47231c` and manager `main`
`1ebe86bef60596ceab81081c6c7c997817899d10`, each `protected: false`.
This is a concrete blocker, not an authorization or permanent assertion about
branch policy. The [audited producer Dockerfile](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/Dockerfile)
and [contract](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/docs/deployment/CONTRACT.md)
remain develop evidence, not protected-main release evidence.

Producer owner must promote reviewed deployment code to `main` through their own
workflow, establish/review branch rules, set public build argument
`VITE_SITE_ORIGIN=https://danil-miftakhov.dev` and run required CI/publication for
the exact source SHA. No producer/infra repository was changed. UID `101:101`,
service `web`, alias `project-router-web`, HTTP 8080 `/en/`, read-only root, bounded
16MiB `/tmp`, cap drop, no-new-privileges, inherited image healthcheck, no host
application ports and no runtime secrets remain the manager contract.
`VITE_SITE_ORIGIN` is build-time data; runtime env cannot repair built HTML.

The independently authenticated release collector must establish all of:

1. Protected-main membership and effective rules for both exact source revisions;
   approved manager SHA and exact manifest/policy content hashes.
2. Successful required checks on each exact SHA with reviewed GitHub App IDs;
   successful same-SHA producer publication on `refs/heads/main`, not a commit tag.
3. Published GHCR immutable digest, raw index/platform-manifest/config bytes with
   descriptor sizes/media/digests, one linux/amd64 target and Engine binding.
4. Cryptographically verified provenance subject, source SHA/repository, allowed
   immutable workflow/builder identity and origin build argument; no label/tag
   fallback. If producer attestations do not bind origin, request an authenticated
   origin-bound attestation workflow or preserve this blocker.
5. Exact OperationCandidate identity including host/daemon, both SHAs, image,
   manifest/policy/plan/gateway/runtime/network/storage/recovery hashes and scope.

ReleaseVerifier tests these relationships after collection; it does not implement
GitHub branch administration or Sigstore authentication. Installation must supply
a reviewed collector with authenticated GitHub/GHCR API reads, rule/check coverage
and provenance signature/trust-root/transparency verification. API denial, missing
attestation, missing protected main, ambiguous workflow or unverifiable build arg
blocks. There is no success-producing default or user-JSON verifier. OCI byte
checks do not prove layer availability, provenance or runtime compatibility.

Before approval, isolated exact-image testing must prove ProjectRouter startup and
restarts under the manager runtime contract, image healthcheck, `/en/`, locale and
asset/404 behavior, built origin metadata, internal network alias and no external
application listener. These real-image tests were **not performed** in 09.4A.
The inactive template stays disabled; no placeholder production digest or active
manifest was added.

## Approval and Stage 09.4B checklist

OperationCandidate hashes every reviewed input. Independent operator authentication
must bind it to operator, nonce and an allowed time window (maximum one hour).
Changed SHA/digest/plan/config/contracts or recovery data require a new approval.
`review_approval` validates review claims only: production issuance, nonce
consumption/replay protection, lock-time revalidation and privileged dispatch are
not implemented. No approval comes from merge, CI, a fixture or `verified: true`.

09.4B requires separate authorization for any host contact and operations, plus:

- Infrastructure-approved installed privileged mechanism and canonical storage/lock,
  no change to `ansible` Docker groups/socket permissions or inferred sudo rights.
- Fresh authenticated complete observations, reviewed host/VPN/Docker IPAM and
  resource absence/ownership, configured runtime/readiness/active-gateway attestors.
- Protected-main source/artifact/provenance/origin evidence for both repositories,
  selected Caddy digest/version, isolated selected-Compose/Caddy/ProjectRouter runtime
  acceptance and official complete-candidate validation.
- Reviewed DNS/ACME/public-port/certificate/backup prerequisites, restore drill,
  console access, explicit first-release no-rollback containment plan and budgets.
- Separate bounded bootstrap and rollout approvals, authenticated operator issuer,
  replay ledger, approved execution window and exact candidate revalidation under
  the canonical lock before intent or mutation.

The infrastructure handoff and external cryptographic/runtime evidence remain
**blocked**, never bypassed. 09.4B must implement/review the actual production
operation wiring; 09.4A deliberately provides no apply command, write transport,
automatic CD, deployment trigger, release/tag or merge.

Offline validation uses `make production-integration-check` and full `make check`
through pinned `.venv`; bootstrap normalization checks local links and EN/RU parity.
Tests use fakes and temporary local flock/SQLite only. Previous Stage 07 15/15 VM
acceptance remains historical evidence; no VM rerun, SSH, Docker mutation,
production image download/publication, Caddy reload, DNS/TLS operation or VDS
filesystem change occurred in 09.4A.

## ProjectRouter v1.0.0 acceptance preparation

A later scoped task prepared an exact-digest disabled
[candidate](candidates/project-router.yaml) outside the real registry and a
separate manual GitHub-hosted image acceptance workflow. Owner-reported release
and attestation results update the historical context above; manager-side live
byte/runtime verification and complete production admission remain pending.
The workflow was not run during implementation. See the
[acceptance handoff](project-router-acceptance.md) for exact release identity,
reviewed trigger, evidence limits and remaining production gates.

## Stage 09.4B.1 acceptance evidence update

ProjectRouter's [isolated acceptance run 39837965499](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39837965499)
is confirmed successful, as is [main Offline CI 39839861288](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39839861288)
at `65af84bd0de23af9cf9ae40b2c0c411e1fbe2c36`. Earlier pending-run statements above
are historical preparation status. The candidate remains disabled, with its original
digest and unchanged admission policy. This success does not mint production trust.

[Caddy acceptance](caddy-runtime-acceptance.md) now pins official 2.11.7 amd64 bytes
and provides a separate explicitly authorized disposable runner harness, with no
host ports/production TLS. Offline, real-Docker and production results are separate.
Unsigned/unverified Caddy provenance, actual production wiring/attestors, full actual
candidate validation and operator bootstrap/rollout approval remain blockers.

Caddy [real acceptance run 39844325743](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39844325743)
passed all 17 isolated cases and cleanup (Caddy 2.11.7 / Engine 28.0.4 / Compose
2.38.2). This supplies disposable runtime evidence only; no production gate is bypassed.

## Stage 09.4B.2 release/source trust update

The [release/source trust implementation](production-release-source-trust.md) now
provides bounded authenticated GitHub protected-source/check/publication collection,
GHCR immutable-byte reads and an adapter to the existing pinned gh/Sigstore
verification. Collector facts bind exact producer/manager SHAs, selected digest,
signer/source identities, signed origin/publish run and manager manifest/policy
bytes. Unreadable protection or incomplete cryptographic evidence blocks. No
collector is installed from API/files or configured by the production preview CLI.

Controlled offline tests prove the composition and failure boundaries. No new
real GitHub/GHCR release verification was authorized/run here. Historical real
acceptance remains separate from release admission. The available official Caddy
statement remains unsigned/unverified; no weaker official-image security policy
is selected. Privileged execution, actual observations, operator authorization,
DNS/TLS/storage/recovery and first rollout retain their separate blockers.
