# Stage 07 — Disposable Ubuntu VM E2E

Infrastructure implemented; **real E2E: not-performed**. No VM, Docker daemon,
Caddy process, image build/pull or real E2E was started during implementation.
Stage 06 PR #6 is merged into `develop`. Stage 08 GitHub Actions and Stage 09
production integration remain Planned. The production registry/policy stay empty.

## Prerequisites and topology

The operator needs Linux amd64 with hardware virtualization and an installed,
working, authenticated [Multipass CLI](https://documentation.ubuntu.com/server/how-to/virtualisation/multipass/index.html).
The host must be able to launch an Ubuntu **24.04 amd64** VM (2 CPUs, 4 GiB RAM,
16 GiB disk) and download Ubuntu packages and pinned Python dependencies. ARM
VMs, containers/LXC, remote Docker contexts and host directory mounts are rejected.
Host Docker and host Caddy are neither required nor invoked.
The local prerequisite check also requires `/dev/kvm` and at least 20 GiB free
on the home filesystem. Verify that the actual Multipass storage pool has room
for the VM and image cache; the local check does not locate that pool. Multipass
must already be installed/authenticated and usable by the invoking user. Its
daemon/driver access, working virtualization and network are checked by the
explicitly confirmed prepare; the CLI does not install or repair the host.

Inside the VM: Ubuntu `docker.io`, `docker-compose-v2`, `caddy`, `python3-venv`,
local Unix Docker socket, root-owned runner, private bridge networks, named test
volumes and SQLite. Cloud-init masks the package's default public Caddy service
and blocks package auto-start through `policy-rc.d` before installation. Docker
is explicitly started during prepare; the separate loopback Caddy unit starts
only during the authorized E2E run. Package versions depend on the Ubuntu archive;
this is a reproducible procedure, not a bit-identical VM image. Record versions
for acceptance; archive/package snapshot pinning is a future improvement.

```text
Host Multipass CLI (no host Docker; no shared mounts)
  -> disposable pd-e2e-<session> Ubuntu VM
       root Python runner -> existing planner / executor / lifecycle
                          -> unix:///run/docker.sock -> private containers
                          -> HTTP to inspected container IPs (private bridge)
                          -> loopback Caddy admin 127.0.0.1:2019
                          -> loopback routes 127.0.0.1:18080
                          -> /var/lib/portfolio-e2e/<session>/SQLite + reports
```

No Docker/Caddy host ports are published. Caddy does not use real DNS, ACME,
TLS, host networking or a Docker socket mount. The VM's normal Multipass
management/networking remains the hypervisor's responsibility. The harness does
not provision or contact a VPS or change the infrastructure repository.

## Modules and trust

| Module | Responsibility |
| --- | --- |
| `scripts/e2e/cli.py`, `orchestration.py`, `host_state.py` | Unified CLI, generated session, default-deny phase confirmations, private receipts/lock snapshots, whole-operation host flock, no retry/resume |
| `scripts/e2e/fixture_bundle.py` | Offline six-role pinned-source build plan and atomic validated raw-manifest import; live builder/publisher unavailable |
| `scripts/e2e/vm.py` | Explicit finite Multipass prepare/status/transfer/invoke; no mount, adoption, delete or purge |
| `scripts/e2e/safety.py` | Session authorization, Ubuntu/virtualization, root marker, hostname, machine-id and pinned Engine ID checks before commands |
| `scripts/e2e/images.py`, `image_lock.py` | Separate test GHCR contract and offline raw-manifest hashing |
| `scripts/e2e/transports.py`, `http.py` | Pinned container/private-network HTTP, Engine worker health, hard process deadline and bounded output; no redirects/proxy environment/DNS |
| `scripts/e2e/observations.py` | Re-admission, bounded complete session inventory, effective runtime/OCI corroboration, active gateway and health sources, closing rediscovery |
| `scripts/e2e/gateway.py` | Actual Caddy `/config/` reader, fixed loopback candidate validation, retained routes, private prior-config snapshots and explicit readiness-gated activation |
| `scripts/e2e/runner.py`, `scripts/e2e_guest.py` | Real scenario orchestration and compact report; same Stage 05 executor and Stage 06 lifecycle/journal |
| `scripts/e2e/cleanup.py` | Opt-in known-session container/empty-network cleanup; preserve volumes, journals, unrelated resources and VM |

`GuestDocker` extends the Stage 05 adapter's environment policy, not its Compose
engine. It reuses scoped `up --no-deps --no-build --pull never`. The only core
integration addition is an optional `discover(..., resolve_image=...)` verifier.
The default Stage 05 verifier and production admission are unchanged.

The composite reader never reads SQLite. It checks the entire **test registry**
and all Engine resources, references, ownership, project/service names, image
configuration defaults, networks, aliases, mounts, drivers and storage attachments.
It rejects refs, unrecognized session applications, unsupported runtime, missing
images, partial inventories and conflicting identities. Runtime declaration
values are accepted only after matching actual Engine configuration and component
identity against the closed admitted fixture contract. A closing inventory and
image comparison rejects changes during sampling. Each source token is issued
only after its own live checks, within one bounded sampling round. It is not a
cryptographic attestation and never establishes global production completeness.

HTTP uses a numeric container IP corroborated against an owned internal bridge
and its membership/subnet. Worker readiness uses `State.Health.Status` and an
actual image healthcheck. No `docker exec curl`, published ports or HTTP response
body enters readiness/journal evidence. The VM host's access to internal bridges
is intentional; private Docker networking does not isolate containers from root.
The host-wide Stage 05 flock is shared by execution, image preparation, cleanup
and gateway activation. It cannot serialize external administrators/Docker clients.

Caddy routes project the Stage 04 `GatewayRoute` contract into a separate test-only
HTTP JSON configuration. The VM host cannot resolve Docker aliases, so upstreams
use inspected private IPs. Actual active host/component/port are matched against
the closed route catalog; actual dial address, upstream and route HTTP checks are
separate evidence. A stale IP produces unverified gateway state. No arbitrary
route editing, TLS or production reload interface is added.

Activation runs under the existing lifecycle lock, rechecks pinned readiness of
all application components, retains every other active route, validates the full
candidate with installed Caddy, saves the prior config, checks it has not changed,
then makes one [Caddy `/load` request](https://caddyserver.com/docs/api).
Invalid validation never reaches load. An unknown load outcome is never retried;
failed post-load verification stops acceptance and requires diagnosis. No automatic
restore/replay is attempted after uncertainty. Prior snapshots and active state
remain available; component readiness alone cannot promote LKG.

## Preparing immutable fixture images

`make e2e-fixtures` is **offline partial preparation**, independent of VM creation
and scenario execution. It does not start a builder or execute Docker, registry
reads or publication. First choose a reviewed **direct linux/amd64 Python 3 base
manifest** (verify platform and config outside this offline tool) and a lower-case
GHCR owner permitted by the test contract. There is no default tag or base digest.

```sh
make e2e-fixtures PYTHON=.venv/bin/python \
  E2E_FIXTURE_OWNER=your-owner \
  E2E_PYTHON_BASE="$REVIEWED_PYTHON_BASE" \
  E2E_SOURCE_DATE_EPOCH=0 E2E_FIXTURE_DIR=/absolute/private-fixture-bundle
```

Set `REVIEWED_PYTHON_BASE` to the complete reviewed `docker.io/library/python@sha256:…`
reference first (all 64 hex digest characters, not the example ellipsis). This creates a
private `plan.json`, `repositories.json` and `context/{Dockerfile,app.py}`, with
SHA-256 source receipts, all six build-argument sets, fixed source epoch, platform,
output requirements and the builder/publisher boundary. It returns **blocked /
exit 2** with reason `disposable-builder-and-publish-not-implemented`, even though
the local bundle is prepared. Repeating identical inputs verifies/reuses the
bundle; changed base, source, owner or epoch requires a new directory. A partial
bundle is retained and never overwritten as a completed plan.

The remaining live prerequisite is a **separately authorized disposable builder**
(not the test VM or primary host daemon), plus a separately authorized publisher.
No safe builder provisioning/ownership or credential-delivery contract exists in
this repository yet; therefore the CLI has no build/publish switch or executor.
The reference infrastructure harness handles Ubuntu VM-image downloads rather
than OCI fixture publication; it cannot supply this missing trust boundary.

On that external builder, use only the copied context and all per-role arguments
from `plan.json`, record Docker/BuildKit/buildx versions and reviewed base platform,
and export single-platform OCI archives. Pin a toolchain supporting
`SOURCE_DATE_EPOCH`; disable provenance/SBOM index wrapping. BuildKit's
[OCI exporter](https://docs.docker.com/build/exporters/oci-docker/) supports archives
and timestamp rewriting; [attestation controls](https://docs.docker.com/build/metadata/attestations/)
describe disabling automatic provenance. Neither the plan nor an identical rebuild
proves a bit-identical artifact. The published exact manifest remains authoritative.

After a separate explicit publication approval, a daemonless publisher may copy
each selected direct manifest/layers to its `ghcr.io/<owner>/pd-e2e-<role>` repository
using [Skopeo](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md)
with digest preservation. Fail on conversion or digest mismatch; do not publish
an OCI index in place of the selected direct manifest. Supply publication credentials
only to that isolated publisher, never as build args, plan fields, CLI arguments,
Git files or test-VM files. For manual GHCR publication use a short-lived PAT classic
with `write:packages`, avoid the broad `repo` scope, satisfy owner/package and SSO
policy, and make all six packages publicly readable. These are the
[GHCR authentication rules](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
There is no GitHub Actions workflow or token creation in this Stage 07 change.
Revoke/dispose of publisher credentials after use; retain artifacts and receipts.

Export the **exact remote raw manifest bytes** into six role-named JSON files,
without formatting/newline changes. Import them locally (same owner/base/epoch):

```sh
make e2e-fixtures PYTHON=.venv/bin/python \
  E2E_FIXTURE_OWNER=your-owner E2E_PYTHON_BASE="$REVIEWED_PYTHON_BASE" \
  E2E_SOURCE_DATE_EPOCH=0 E2E_FIXTURE_DIR=/absolute/private-fixture-bundle \
  E2E_RAW_MANIFESTS=/absolute/exported-raw-manifests
```

Only after all six validate does `artifacts/lock.json` become visible with the
unaltered raw manifests. Import is offline: `status=passed` means **local contract
verified**, not image build/publication, availability, platform execution or
producer provenance verified. Failed imports retain staging evidence and never
expose an invalid final artifact set. Repeated import verifies the existing set;
different bytes cannot replace it. The VM will independently pull each exact
digest and corroborate Engine/platform/config before a real run.

Image artifacts have **not been built or published here**. Confirmed fixture
digests cannot be invented while real Docker operations are prohibited. The
operator must first supply six public test-only GHCR artifacts, each a distinct
**direct linux/amd64 OCI/Docker schema-2 manifest**, plus its exact raw manifest
bytes. No credentials are copied into the VM. The contract only allows
`ghcr.io/<owner>/pd-e2e-<name>@sha256:<digest>`; production repositories, tags,
indexes, unknown roles, path traversal and malformed descriptors are rejected.

Source is in [fixture Dockerfile](../e2e/images/Dockerfile) and
[app.py](../e2e/images/app.py). Build/publish separately on an explicitly authorized
disposable image-builder; this is never part of execution or `e2e-test`.
Use an approved immutable `PYTHON_BASE` direct linux/amd64 Python 3 manifest and
BuildKit with fixed source timestamps (for example `SOURCE_DATE_EPOCH`), no
provenance/SBOM index wrapping, and record toolchain/base digests. Rebuilding is
not assumed to produce the same digest; deployment always uses the recorded
published artifact, never a rebuilt tag.

| Role / raw file | Build arguments | Expected behavior |
| --- | --- | --- |
| `v1` / `v1.json` | `VERSION=v1 MODE=http USER_ID=10001` | HTTP `/health`: 200, body `v1` |
| `v2` / `v2.json` | `VERSION=v2 MODE=http USER_ID=10001` | HTTP `/health`: 200, body `v2` |
| `bad` / `bad.json` | `VERSION=bad MODE=bad USER_ID=10001` | HTTP `/health`: 503 |
| `worker` / `worker.json` | `VERSION=worker MODE=worker USER_ID=10001` | In-image Docker healthcheck observes worker marker |
| `data1` / `data1.json` | `VERSION=v1 MODE=data USER_ID=0` | Create `/data/marker` once, expose `/data` |
| `data2` / `data2.json` | `VERSION=v2 MODE=data USER_ID=0` | Preserve the existing named-volume marker |

Root is deliberate only for the two persistent fixtures, to initialize an empty
root-owned volume without an extra privileged init service or volume chmod.
They have no public ports, socket/bind mounts or privileged mode and are test
artifacts, not application hardening examples. All other fixtures run non-root.
The HTTP fixture's image healthcheck is not its HTTP readiness gate; HTTP uses
private network probes. Only the worker's Engine healthcheck qualifies it.

For each published direct manifest, export exact bytes using the image producer's
registry tool (for example `docker buildx imagetools inspect --raw <published-ref>`).
Do not pretty-print raw files: changing bytes changes the digest. Supply a local
`repositories.json` mapping all six roles to their GHCR repository without tag or
digest. The offline helper derives manifest references from the actual bytes:

```sh
.venv/bin/python -m scripts.e2e.image_lock \
  --repositories /absolute/fixture-artifacts/repositories.json \
  --directory /absolute/fixture-artifacts
```

It creates `lock.json` exclusively. This validates content/descriptor structure,
not publication or provenance. During explicit image preparation the VM pulls each
exact reference, checks RepoDigests, platform and raw manifest `config.digest`
against the Engine config image ID. The manifest digest is **never** replaced by
that config ID. This mapping works without an optional Engine `Descriptor` field;
indexes, signatures and provenance verification remain unsupported. All images
must already be verified/preloaded when deployment starts; missing images block.

## Unified run — only after separate authorization

After the artifact preparation above, run the committed revision from a real TTY:

```sh
make e2e-prerequisites PYTHON=.venv/bin/python
make e2e PYTHON=.venv/bin/python \
  E2E_IMAGES=/absolute/private-fixture-bundle/artifacts/lock.json
make e2e-report PYTHON=.venv/bin/python
```

`make e2e` is an orchestrator of the existing `VM.prepare`, transfer/images and
run paths, not another deployment engine. It validates all images offline,
checks local prerequisites, generates a unique 16-hex session and freezes the
lock/raw bytes under `.e2e-state/<session>/`. `E2E_SESSION` does not select or reuse
a session here. Override `E2E_STATE_DIR` with an absolute private directory if
needed. Keep custom state/fixture directories outside the source checkout. The
default `.e2e-state` and `.e2e-fixtures` directories are Git-ignored and excluded
from `make fix`/bootstrap traversal so exact raw manifest bytes stay unchanged.
The session, VM name, receipt path and exact diagnosis command are printed
before VM work. Four prompts require the exact printed tokens, with default deny:

1. `prepare:<session>` — new 2 CPU / 4 GiB / 16 GiB Ubuntu VM and downloads.
2. `images:<session>` — six immutable public pulls into that VM's daemon.
3. `run:<session>` — real Docker scenario mutations using preloaded images.
4. `activate:<session>` — readiness-gated loopback Caddy activation for that run.

Non-TTY, EOF, blank input, `yes` or a wrong token cannot authorize a phase. Existing
environment tokens for individual commands do not bypass these prompts. Declining
after VM creation retains the VM and blocks another orchestration in this state
directory. No phase runs twice; there is no automatic resume or fallback. A fully
passed run permits a later `make e2e` to create a fresh VM/session, keeping old ones.

Host state uses mode-0700 directories, mode-0600 bounded receipts, atomic fsynced
writes and a persistent nonblocking `controller.lock` for the whole operation.
Do not unlink it. A phase intent is saved **before** a mutating call; timeout,
SIGKILL or uncertain response leaves an unfinished/unknown receipt and blocks a
new run in that directory. The pinned VM session/machine/daemon identity is checked
again after each confirmation. No locks, resources or journal are reset after
failure. The guest's existing host flock still protects its engine operations.

`make e2e-report` reads the validated **local** `current.json`/session receipt and
does not contact Multipass or establish live success. The generated command
`make e2e-diagnose E2E_SESSION=<session> E2E_AUTHORIZE=diagnose:<session>` reads the
guest report/identity. If preparation failed before the guest tools were installed,
use the explicit `e2e-status` metadata path first; do not retry prepare as if absent.
The host receipt and VM retain all evidence; no cleanup runs on an error. An
unknown guest outcome needs manual diagnosis even if the host process has exited.

## Individual commands — only after separate authorization

Install the pinned local development dependencies as in the README. The following
sequence is **not authorization** and was not executed. Use a new session for each
VM/run; an existing name is never adopted, overwritten, resumed or recreated.
Use the committed source revision intended for testing: prepare transfers only
allowlisted Python/schema files and dependencies, not Git history, home files or
credentials. Source revision and archive SHA-256 are recorded in the VM.

```sh
export E2E_SESSION="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
make e2e-prerequisites PYTHON=.venv/bin/python
make e2e-prepare PYTHON=.venv/bin/python E2E_AUTHORIZE="prepare:$E2E_SESSION"
make e2e-status PYTHON=.venv/bin/python
make e2e-images PYTHON=.venv/bin/python \
  E2E_AUTHORIZE="images:$E2E_SESSION" E2E_IMAGES=/absolute/fixture-artifacts/lock.json
make e2e-test PYTHON=.venv/bin/python \
  E2E_AUTHORIZE="run:$E2E_SESSION" E2E_GATEWAY_AUTHORIZE="activate:$E2E_SESSION"
make e2e-diagnose PYTHON=.venv/bin/python E2E_AUTHORIZE="diagnose:$E2E_SESSION"
# Explicit optional cleanup after evidence has been inspected:
make e2e-clean PYTHON=.venv/bin/python E2E_AUTHORIZE="clean:$E2E_SESSION"
```

All host commands route through `python -m scripts.e2e.cli`; the VM adapter CLI
remains compatible. `e2e-prerequisites` is a local binary/platform/KVM/disk check
and needs no session; `e2e-status` reads Multipass
metadata and rejects host mounts. Preparation explicitly downloads VM/packages
and Python requirements; images explicitly pulls only the supplied immutable test
artifacts. Test never provisions missing dependencies/images. Missing opt-ins fail
before commands. Different prepare/images/run/activate/clean tokens cannot be
substituted for each other. They are acknowledgements, not secrets.

VM marker, machine-id, Ubuntu version, virtualization, hostname, local socket,
Engine name and pinned Engine ID are checked before mutations; inherited Docker
host/context/TLS/Compose overrides are rejected. Root's Docker access is a VM host
administration boundary. There is no Docker group grant to the ordinary VM user.
Multipass itself requires its separately installed host trust/management setup.

Interrupted or failed prepare is never retried as absence. Inspect that VM;
there is no resume/install-over-existing command. Run is single-use. The runner
saves its report after each scenario and handles SIGTERM/interrupt without cleanup.
SIGKILL cannot save a final report, so inspect the last report, live resources and
journal. Loss of the host Multipass connection does not prove the guest controller
stopped: diagnose the guest before any further operation; never blindly retry. Unfinished runs remain unfinished. Source/identity/registry, baseline,
reports, gateway snapshots and journals live under private VM-owned paths.

## Scenario evidence and reports

`make e2e-check` runs **offline** tests for transport/policy boundaries, fake VM
lifecycle, complete-source rejection, fake Engine + actual planner/executor/
lifecycle + temporary SQLite, rollback, candidate retention, cleanup, failures
and interrupts. Ordinary `make check` includes these offline tests and never runs
VM, Docker, Caddy or real scenarios. Simulated tests always report `mode=offline`
and `realE2E=not-performed`.

| # | Real scenario | Required evidence |
| --- | --- | --- |
| 1 | Registration/deployment | Separate admitted registry, actual containers and HTTP v1 response |
| 2 | Readiness before release | Verified per-component journal/LKG, worker Engine health |
| 3 | Route activation | Actual Caddy config and reachable private upstream/host route after readiness |
| 4 | Backend-only v2 | Backend ID changes, HTTP v2, frontend/worker IDs and Caddy config unchanged |
| 5 | No-op twice | No Compose calls, same IDs and journal attempts |
| 6 | Failed readiness | Real bad HTTP image; failed target never becomes LKG |
| 7 | Stateless rollback | Previous exact image/runtime and full application verified, LKG preserved |
| 8 | Persistent preservation | Real named-volume marker survives image/container replacement |
| 9 | Invalid Caddy | Official candidate validation fails, active config unchanged |
| 10 | Partial/unknown | Real Compose mutations followed by injected unknown response; no retry, later component untouched |
| 11 | Controller crash | Real SIGKILL after durable intent, actual up, or verified readiness before journal success |
| 12 | Recovery analysis | Repeated fresh analysis of five unfinished journals; no mutation/promotion/replay |
| 13 | Ownership conflict | Actual intentionally foreign container name; zero deployment mutation; only fixture receipt allows its removal |
| 14 | Concurrent callers | Second actual executor invocation blocked by the fixed host flock |
| 15 | Unrelated preservation | Sentinel application IDs, separate running unrelated container and all baseline resource IDs preserved |

Unknown-response injection models loss of controller acknowledgement after an
actual Engine operation; it does not claim a physical daemon/network crash. The
lock case uses independent lock descriptors in one process, while crash cases
use independent forked controller processes. No high-availability claim is made.
Separate private journals isolate deliberate unfinished scenarios; they all use
the same fixed host lock. This is test case isolation, not a production bypass of
Stage 06's unfinished-run gate. Persistent rollback remains disallowed.

The compact private `report.json` distinguishes `passed`, `failed`, `blocked`,
`not-performed`, with per-scenario evidence and a real/offline mode. A failed
scenario stops dependent scenarios; unattempted cases stay `not-performed`.
A blocked prerequisite does not count as successful E2E. The host CLI returns
nonzero for a non-passing report. Diagnose reads identities and the saved report;
raw environment, HTTP bodies, health logs and secrets are never printed.

## Cleanup and remaining limits

Cleanup is a separate opt-in, never a finally/exit trap. It requires a complete
fresh inventory, baseline and known admitted artifacts, exact names/labels/project
identity, no preexisting target IDs or foreign network attachments, then rechecks
each resource before stop/removal. It does not use prune/down, unlink the fixed
lock, delete volumes/data, reset journals, remove images, delete the VM or stop an
unknown Caddy. Caddy stays loopback-only with its diagnostic configuration; all
snapshots and volumes remain. The unrelated fixture is intentionally preserved.
A leftover foreign fixture after interruption causes cleanup to refuse; inspect
its receipt rather than adopting or deleting by name. VM disposal/data destruction
is a separate manual operator decision, with no delete command in this harness.

Ordinary no-op uses the existing executor without new journal attempts. The
Stage 06 runtime hash now re-canonicalizes after removing images, preventing
image-dependent array ordering from spuriously invalidating stateless rollback.
Old journal hashes are not migrated or reinterpreted; mismatches block safely.

Live evidence assumes an honest dedicated VM administrator/Engine/Caddy and a
quiescent test host; repeated reads are not an atomic multi-source transaction.
No production ref delivery, OCI index mapping, signatures, backups, disk-loss
recovery, TLS/DNS, remote observation, production execution policy or shared-host
isolation is established. Stage 08 adds separately scoped offline CI/automation;
Stage 09 needs reviewed production source/permissions, real host evidence,
configuration/secrets delivery, artifact retention and operator acceptance.
