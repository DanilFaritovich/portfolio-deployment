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
| `scripts/e2e/fixture_bundle.py` | Offline six-role source/base plan, exact raw-manifest import and fsynced atomic image lock |
| `scripts/e2e/fixture_execution.py`, `fixture_guest.py` | Separately confirmed disposable builder/publisher VMs, daemonless Buildah/Skopeo, bounded artifacts, isolated credentials, digest-preserving publication and anonymous verification |
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

First and subsequent runs use one command, with no environment exports:

```sh
make e2e
```

Install the pinned development dependencies first (see README); if they are in
`.venv`, pass `PYTHON=.venv/bin/python` to Make. A real terminal is required.
Without a ready `~/.local/share/portfolio-deployment/e2e-fixtures/artifacts/lock.json`, the command offers preparation
of six images (`prepare-fixtures`, default deny), then requests every separate
approval listed below. A verified existing lock skips build/publication and goes
straight to VM prepare → immutable pulls → real E2E. An invalid lock or an
unfinished fixture/E2E receipt stops the run; it never triggers a rebuild.

The GHCR owner comes from this checkout's single verified GitHub `origin` fetch
and push URLs (HTTPS, scp-style SSH or `ssh://git@github.com/`). Both must name the
same repository; unknown hosts, credentials in URLs, multiple URLs or conflicting
identities fail closed. The owner is normalized to lowercase. `E2E_FIXTURE_OWNER`
overrides the target owner after origin verification. All `E2E_*` settings are
optional overrides. Private defaults are absolute paths based on the current user's
home: `~/.local/share/portfolio-deployment/e2e-fixtures/` and
`~/.local/share/portfolio-deployment/e2e-state/`. No exports are required.
`make e2e`, `e2e-fixtures` and `e2e-report` share this path mechanism; individual
VM commands continue to use explicit session IDs and guest-side state.

The reviewed source in `e2e/python-base.json` is Docker Official Image
`docker.io/library/python:3.13-slim-bookworm`, maintained at
[Docker Library Python](https://github.com/docker-library/python).
After initial confirmation the host reads anonymous registry metadata only, with
no image layer pulls or Docker daemon. It selects exactly one direct linux/amd64
manifest, checks its raw digest/size, config digest/size, platform and Python 3
version metadata, then fsyncs `python-base.json` in the private fixture directory.
The immutable direct reference drives `plan.json` and every build argument; the
tag is never used after pinning. The builder independently rechecks the pinned
manifest/config/platform before any build. `E2E_PYTHON_BASE` may override this with
an official `docker.io/library/python@sha256:<64 hex>` direct manifest; it is
validated in the same way. No current digest is fabricated or committed here.

### First build and publication — operator only

To prepare fixtures separately, without running the test VM:

```sh
make e2e-fixtures
```

This uses the same defaults, source resolution and confirmations as `make e2e`.
`E2E_FIXTURE_EXECUTE=yes` / CLI `--execute` remains accepted, but never bypasses
interactive approvals. For an offline source/base plan only, supply an immutable
base explicitly:

```sh
.venv/bin/python -m scripts.e2e.cli fixtures --plan \
  --base docker.io/library/python@sha256:REVIEWED_DIRECT_MANIFEST_DIGEST
```

Replace the placeholder with a reviewed 64-hex digest. Offline planning returns
blocked/exit 2 (`fixture-execution-requires-separate-opt-in`); it never resolves a
tag or contacts Multipass/registries. Repeating identical inputs verifies the
bundle. Changed owner/base/epoch/source needs a new directory, retaining old
evidence; never bypass an unknown operation by selecting fresh state.
The source epoch defaults to 0 (`E2E_SOURCE_DATE_EPOCH` override).
Real fixture operations and real E2E remain **not-performed** in implementation.

Requirements: a real TTY, Linux amd64/KVM, authenticated Multipass, Ubuntu/package
and registry network access, at least 64 GiB free host disk for fixture work, and
capacity for two 2 CPU / 4 GiB / 24 GiB VMs. The later test VM needs additional
capacity; its own preflight requires 20 GiB free host disk. No host Docker daemon
or E2E VM is used for fixture creation. The command prints a unique session,
both VM names and the private `execution.json` path. Each phase prints its session,
VM, action and consequences, then asks `Continue? [y/N]:`. Only `y`/`yes`
(case insensitive, whitespace trimmed) approve that phase; blank/EOF/non-TTY and
other answers deny it. The following operation identifiers are labels, not text to type:

1. `builder-prepare:<session>` creates a NEW `pd-fixture-builder-<session>` Ubuntu
   24.04 VM and installs Buildah/Skopeo. Only the pinned plan, minimal source
   context and frozen standalone payload enter it; no credentials or host mounts.
2. `build:<session>` checks source hashes, fetches exact raw base manifest/config,
   verifies their digests and linux/amd64 platform, then builds all six roles in
   the builder. Buildah uses the fixed epoch, disables push/pull retries and keeps
   intermediate containers. Versions, base bytes, per-role intents/results and
   outputs remain in `/var/lib/pd-fixtures` inside the builder. The exported
   bounded `images.tar` is copied back, hashed and validated locally, including
   blobs/config/platform and a private candidate lock under `raw/`.
3. `publisher-prepare:<session>` creates a NEW isolated
   `pd-fixture-publisher-<session>` VM with Skopeo. It receives the plan, standalone
   payload and exported image archive, without builder storage or host mounts.
4. Before `publish:<session>`, use the **separate terminal** printed by the tool:

   ```sh
   multipass shell pd-fixture-publisher-SESSION
   # Inside that publisher VM only; enter credentials at Skopeo's hidden prompt:
   sudo skopeo login --authfile /run/pd-fixtures-auth.json ghcr.io
   ```

   Use a short-lived PAT classic with `write:packages`, owner/package permission
   and required SSO authorization; avoid broad repository/admin scopes. Follow
   [GHCR authentication requirements](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
   Never put a token in host environment variables, CLI arguments, build arguments,
   plans, reports, staging or Git. Answer `y` for `publish:<session>` in the original
   terminal only after login and reviewing all six printed target repositories. Skopeo copies each selected manifest with
   [`--preserve-digests`](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md),
   uses a unique `fixture-<session>` tag, then verifies the remote raw bytes by
   immutable digest. A conversion, changed byte or failed command stops the attempt.
5. After all pushes, make all six `pd-e2e-{v1,v2,bad,worker,data1,data2}` packages
   **public** in GitHub package settings. Then answer the separate `y/N` prompt
   for `verify-public:<session>`.
   A fresh empty auth file is used to fetch every manifest/config anonymously,
   verify exact bytes/config digest and linux/amd64. Only after all six pass is
   `artifacts/lock.json` atomically exposed with the unmodified raw manifests.

The selected artifacts are OCI-compatible **Docker schema-2 direct manifests**.
Buildah's Docker format retains the worker `HEALTHCHECK`; export fixes gzip layers
and schema-2 before any manifest digest is selected. Publication preserves that
export verbatim. There is no index, attestation wrapper, image-ID substitution or
assumption that rebuilding produces identical bytes. The image contract continues
to accept direct OCI and Docker schema-2 manifests; indexes remain forbidden.

Normal publisher completion or failure removes **only** the temporary auth file.
Revoke the PAT after the attempt. SIGKILL, VM loss or a failure before credential
handling can leave it in publisher `/run`; revoke it immediately and manually
inspect that publisher. No publication credential reaches the builder, host
artifact bundle or test VM. Guest commands use a fixed environment, deadlines,
bounded output and sanitized errors; raw registry errors are not reports.

The whole execution, including final lock creation, holds the existing persistent
nonblocking flock. Fsynced host intents precede operations; guest per-phase/per-role
intents prevent replay. Disconnect, timeout, interrupt, partial publication or
unfinished intent preserves archives, raw bytes, VM storage and receipts and
blocks another execution in that directory. There is **no retry, resume, reset,
VM deletion, prune, volume removal or automatic evidence cleanup**. Inspect
`execution.json` and the retained VM evidence before planning any new attempt;
do not use a fresh directory to bypass an unknown outcome. A completed repeat
verifies and returns its existing lock without VM access/build/push. Builder and
publisher VMs remain; an operator may explicitly stop them to free RAM before E2E,
without deleting storage/evidence.

### Offline import remains available

An external producer can still supply six exact raw manifests, named by role.
With the same owner/base/epoch inputs, use:

```sh
make e2e-fixtures PYTHON=.venv/bin/python \
  E2E_PYTHON_BASE="docker.io/library/python@sha256:REVIEWED_DIRECT_MANIFEST_DIGEST" \
  E2E_RAW_MANIFESTS=/absolute/exported-raw-manifests
```

Import and execution opt-in are mutually exclusive. Import is offline:
`status=passed` proves only the local image contract, without claiming build,
publication, public access, platform execution or provenance. Failed imports
retain staging evidence. Different bytes cannot replace an existing artifact set.
The test VM independently pulls each locked digest and corroborates
Engine ID/config/platform/RepoDigests before scenarios. Production admission is
unchanged, and no credentials enter the test VM.

Source is in [Dockerfile](../e2e/images/Dockerfile) and [app.py](../e2e/images/app.py).
**Real builds, publication and E2E: not-performed.** No confirmed digest is invented.

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

Run the committed revision from a real TTY (missing fixtures are offered first):

```sh
make e2e-prerequisites PYTHON=.venv/bin/python
make e2e PYTHON=.venv/bin/python
make e2e-report PYTHON=.venv/bin/python
```

After successful fixture publication, `make e2e` automatically uses
`$E2E_FIXTURE_DIR/artifacts/lock.json` (default `~/.local/share/portfolio-deployment/e2e-fixtures/artifacts/lock.json`).
`E2E_IMAGES=/absolute/lock.json` remains an explicit override. With this verified lock it skips build/publication; the lock drives Multipass
prepare → pulls → real scenarios. Without it, initial preparation is offered.

`make e2e` is an orchestrator of the existing `VM.prepare`, transfer/images and
run paths, not another deployment engine. It validates all images offline,
checks local prerequisites, generates a unique 16-hex session and freezes the
lock/raw bytes under `~/.local/share/portfolio-deployment/e2e-state/<session>/`.
`E2E_SESSION` does not select or reuse a session here. `E2E_STATE_DIR` and
`E2E_FIXTURE_DIR` may override these defaults (absolute paths recommended; relative
overrides remain relative to the invocation directory). Keep custom directories
outside the checkout. A single shared path module creates missing parents and
state directories with `0700`, using pinned directory descriptors and no-follow
opens; private files use `0600`. All existing components retain strict ownership
and permission checks, including the unchanged `private_directory()` validator.
Existing safe `.local`/`share` ancestors may be `0755`; private leaf directories
must be owned by the current user with no group/other access. Symlinks, `..`,
foreign owners and writable unsafe ancestors are rejected. Root-owned sticky
system ancestors retain the existing validator's exception (for example `/tmp`).
No chmod/chown is applied to HOME, Work, the checkout or any existing directory.
A checkout with mode `775` works because private state no longer traverses it.
Legacy directories remain Git-ignored and excluded from normalization so their
exact evidence is preserved. Path failures return specific `e2e-path-*` codes and
safe guidance instead of the generic prerequisite error; no private path values,
file contents or tokens are printed by path diagnostics.
The session, VM name, receipt path and exact diagnosis command are printed
before VM work. Four separate `Continue? [y/N]:` prompts default to deny:

1. `prepare:<session>` — new 2 CPU / 4 GiB / 16 GiB Ubuntu VM and downloads.
2. `images:<session>` — six immutable public pulls into that VM's daemon.
3. `run:<session>` — real Docker scenario mutations using preloaded images.
4. `activate:<session>` — readiness-gated loopback Caddy activation for that run.

Only `y` or `yes` (case insensitive, whitespace trimmed) authorize the displayed
phase. Non-TTY, EOF, blank input, `n`, operation tokens and `yes to all` deny it. Existing
environment tokens for individual commands do not bypass these prompts. Declining
after VM creation retains the VM and blocks another orchestration in this state
directory. No phase runs twice; there is no automatic resume or fallback. A fully
passed run permits a later `make e2e` to create a fresh VM/session, keeping old ones.

Host state uses mode-0700 directories, mode-0600 bounded receipts, atomic fsynced
writes and a persistent nonblocking `controller.lock` for the whole operation.
Do not unlink it. A phase intent is saved **before** a mutating call; timeout,
SIGKILL or uncertain response leaves an unfinished/unknown receipt and blocks a
new run, including one selecting a different output directory. A fixed private
`~/.local/share/portfolio-deployment/e2e-control/controller.lock` also serializes
unified fixture/E2E operations and tracks their receipt directory. The default
fixture and E2E receipts are inspected even when overrides select new paths. The pinned VM session/machine/daemon identity is checked
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
each resource before stop/removal. The host first obtains a read-only exact
kind/name/ID list and asks a separate `y/N` confirmation. Guest cleanup compares
the newly observed full list against that approval under lock; a changed list
blocks removal. It does not use prune/down, unlink the fixed
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


## Legacy state transition

Before accessing the new host stores, unified commands check `.e2e-state` and
`.e2e-fixtures` in the source checkout and invocation directory. Any nonempty,
symlinked or unreadable legacy directory returns
`e2e-legacy-state-review-required`, including when overrides select another path.
Even apparently completed contents require review: a single `passed` field does
not prove that all receipts and VM operations are complete. Directory entries are
checked without reading or printing journal contents. Empty legacy directories
are left untouched. No automatic move, deletion, permission repair, retry, reset
or cleanup is implemented. `e2e-report` also reports this transition blocker;
legacy receipts must be inspected locally by the operator.

Manual procedure (operator only):

1. Stop all controllers and ensure no operation is still running. Retain the
   persistent `controller.lock` files; never unlink a lock to bypass a blocker.
2. Inspect both legacy directories locally: E2E `current.json` and session receipts,
   fixture `execution.json`, pending files, plans, pinned identity and immutable
   image-lock/raw artifacts. Keep private evidence out of shared logs. Check the
   referenced VMs through separately authorized diagnosis if necessary. Missing,
   corrupt or unknown evidence is unresolved, not a fresh-run authorization.
3. If an operation is unfinished/unknown, stop here. Keep all state and VM identity
   evidence; obtain a separate reviewed resolution. Moving evidence aside or choosing
   a fresh override is not recovery and must not be used to retry that operation.
4. Only after confirming that no mutation occurred, or after explicitly resolving
   all prior operations, choose a safe owned destination below the new private
   base. Manually relocate each entire reviewed state directory without replacing
   or merging any existing destination. Prefer a same-filesystem rename while all
   controllers are stopped, preserving lock inodes and exact receipts/artifact bytes.
   If defaults already exist, select unused destinations through the overrides.
   Check that private directories/files are `0700`/`0600`, owned by you, and contain
   no symlinks. Do not change HOME, Work, checkout or foreign-directory permissions.
   Keep a private evidence archive for a reviewed attempt that never mutated; never
   discard receipts to create the appearance of a clean run.
5. After both legacy locations have been reviewed and relocated, use
   `make e2e-report` with the selected state path. Unfinished migrated receipts still
   block execution. Verify any ready fixture lock before `make e2e`; every real
   operation still requires its separate confirmation. No command resumes or resets
   a previous session automatically.


## Snap Multipass file exchange

Private state remains below `~/.local/share/portfolio-deployment/`. The Snap client
opens only verified staging copies in the nonhidden
`~/PortfolioDeploymentE2E/<session>/<role>-<unique-operation>/` tree. Every staging
directory is private `0700`, every file `0600`; no symlinks, traversal, foreign
owners, unsafe writable parents or hardlinked files are accepted. Operation
subdirectories are exclusively created and never reused. Builder, publisher and
E2E cloud-init, source archives, image locks/manifests and image archive transfers
all use this exchange, including guest-to-host downloads. No host `/tmp` or hidden
path is sent to Multipass. Guest `/tmp` remains inside the disposable VM.

Copies are bounded to 4 GiB, streamed, hashed and fsynced. Snap access is checked
with a fixed shell script through `snap run --shell multipass`: it opens the staged
file for read/write and corroborates exact size and SHA-256. Uploaded artifacts
are checked for guest type/ownership/size/hash before install or extraction;
downloads corroborate the guest descriptor, retained staging copy and private
copy. Raw manifests and OCI blobs retain their additional contract validation.
Staging retains per-transfer receipts and all bytes on success, failure and
unknown; nothing is automatically removed. GHCR credentials never enter staging;
login remains inside the publisher VM only.

For `multipass-staging-access-denied-check-snap-home-permission`, independently
inspect `snap connections multipass`. If `home` is disconnected, an operator may
separately authorize `sudo snap connect multipass:home`; the tool never changes
Snap permissions itself. Check the retained staging file and its private parent
ownership/modes. Do not create a symlink into `.local` or relax HOME permissions.
A failed probe stops before the corresponding launch/transfer; no retry occurs.

Transport failures have separate `multipass-list-*`, `multipass-info-*`,
`multipass-launch-*`, `multipass-exec-*`, `multipass-transfer-*`,
`multipass-cloud-init-*`, `multipass-identity-*` and staging codes. Safe stderr
evidence is at most 256 characters of allowlisted diagnostic fragments extracted
from at most 8192 bytes. Arbitrary lines, command arguments, URLs, tokens and raw
guest logs are discarded, including unrecognized diagnostic text.

## Closing an unknown builder preparation

### Existing builder: session `b616980ef1c99192`

Operator-reported evidence: Ubuntu 24.04 / cloud-init 26.1 finished `degraded done`;
system schema validation reported `E1: 700 is not of type 'string'` in `runcmd`.
Buildah/Skopeo and `/etc/pd-fixture.json` exist, and builder
`pd-fixture-builder-b616980ef1c99192` is running. The original receipt is `unknown`.
These are operator observations, not a successful fixture build or E2E acceptance.

The current source used Python string `"700"`, which PyYAML serializes as `'700'`;
an unquoted YAML `700` loads as an integer and violates the
[runcmd string-argument schema](https://docs.cloud-init.io/en/26.1/reference/modules.html#runcmd).
The exact conversion point in this incident is unverified: compare retained
private YAML, Snap staging YAML and guest user-data before attributing it to a
serializer or Multipass. New builder/publisher configs use `u=rwx,go=` (0700).
Both generators check argument types after YAML serialization. Before staging or
launch, they also run `cloud-init schema --config-file` if the local binary is
installed; failure blocks launch. No binary is downloaded. Without it, only the
argument-type check runs; this is not full schema validation. A host schema pass
does not prove the guest's merged configuration or boot success. Nonzero guest
`cloud-init status --wait`, including degraded exit 2, still aborts preparation.

Safe incident handoff/closure procedure for this specific session (operator-only;
no commands below were run during this fix):

1. Stop all host E2E/fixture controllers and independently verify that no launch,
   transfer, exec or cloud-init operation is still running. Do not rerun `make e2e`,
   preparation, `cloud-init init/clean`, or guest `runcmd`. Do not edit the original
   YAML, marker, receipts, `.pending` files, fixed fence or lock inodes. Do not use
   a new directory to bypass the global fence.
2. Preserve the original private fixture directory and
   `~/PortfolioDeploymentE2E/b616980ef1c99192/` staging tree in place. Create a
   separate private evidence directory (0700; files 0600). Copy the receipt,
   generated cloud-init, plan, transfer receipts and staged YAML without changing
   the originals; record SHA-256, paths, ownership/modes and collection time.
   Keep raw evidence private and out of Git/PR logs.
3. After separately authorizing read-only VM diagnosis, save output and exit
   status of each command below in that evidence directory. Use the original
   `E2E_FIXTURE_DIR` override on the make command if one was used:

   ```sh
   make e2e-fixture-diagnose PYTHON=.venv/bin/python E2E_SESSION=b616980ef1c99192
   multipass list --format json
   multipass info pd-fixture-builder-b616980ef1c99192 --format json
   multipass exec pd-fixture-builder-b616980ef1c99192 -- sudo cloud-init status --long
   multipass exec pd-fixture-builder-b616980ef1c99192 -- sudo cloud-init schema --system --annotate
   ```

   Exit 2 from status is evidence of degradation, never permission to continue.
   Read-only collection should also preserve `/var/log/cloud-init.log`,
   `/var/log/cloud-init-output.log`, `/var/lib/cloud/instance/user-data.txt`,
   `/var/lib/cloud/instance/cloud-config.txt`, `/var/lib/cloud/instance/runcmd`,
   `/etc/pd-fixture.json`, hostname and machine-id. Corroborate identity using the
   marker/session/kind, machine-id, hostname and inventory; a name alone does not
   authorize adoption or deletion. Hash the collected evidence and record the
   original receipt's canonical `receiptSha256` from diagnosis.
4. Record an operator incident decision outside the controller receipts: builder
   preparation failed validation, VM retained for diagnosis, no build/publication
   authorized, no replay. This completes evidence handoff, **not** controller
   session closure. Keep the session `unknown` and the global execution fence.
   Do not invoke `e2e-close-unknown`: its absent-builder decision would be false
   for this incident. Stopping or deleting the VM would not establish that no VM
   was created, either.
5. Controller closure remains blocked while this VM exists. A separately scoped,
   reviewed recovery change is required to record an evidence-bound terminal
   decision for an existing failed builder, retaining the original unknown
   receipt and preventing replay. Any later VM stop/disposal and any fresh
   session require separate explicit authorization; this procedure authorizes
   neither. Do not remove the VM merely to satisfy the absent-builder check.

### Absent builder only: earlier session `4d51ffc5eb90ba5a`

Session `4d51ffc5eb90ba5a` must remain in its original directory, with its original
`execution.json`, cloud-init and lock. The following is an operator procedure;
none of these live/read-only Multipass commands was run as part of this fix.
Closure supports **only** unknown `builder-prepare`, with no pinned identities,
build result, image archive, exported manifests, publication or pending receipt.
Other phases remain blocked for a separately scoped recovery procedure.

1. Stop all E2E/fixture controllers and independently verify no Multipass
   launch/exec/transfer/cloud-init operation is unfinished. Process exit or an
   empty inventory alone is insufficient. Review the original receipt, any
   `.pending` files, cloud-init and Snap failure evidence. Do not unlink locks,
   rename state out of the way, delete artifacts or repeat preparation.
2. Independently inspect `multipass list --format json`, including stopped/deleted
   instances. There must be no builder, publisher or E2E VM for this session.
   Read the saved phase and fresh inventory without guest execution:

   ```sh
   make e2e-fixture-diagnose E2E_SESSION=4d51ffc5eb90ba5a
   ```

   For a custom original directory, pass `E2E_FIXTURE_DIR=/absolute/original/path`.
   Retain the printed `receiptSha256` (canonical JSON hash, not a raw-file hash).
3. Only after independent checks, create a private `0600` JSON evidence file in
   the original private fixture directory, with these exact fields. Replace
   `HASH_FROM_DIAGNOSIS`; set the attestations to true only when verified:

   ```json
   {
     "session": "4d51ffc5eb90ba5a",
     "receiptSha256": "HASH_FROM_DIAGNOSIS",
     "controllersStopped": true,
     "receiptsReviewed": true,
     "noUnfinishedMultipassOperations": true,
     "vmInventoryIndependentlyChecked": true
   }
   ```

4. Request closure in a real TTY, using the same original directory:

   ```sh
   make e2e-close-unknown E2E_SESSION=4d51ffc5eb90ba5a E2E_EVIDENCE="$HOME/.local/share/portfolio-deployment/e2e-fixtures/independent-review.json"
   ```

   Review the shown session/VM/decision and answer `y` only if correct. Under the
   fixed host fence and original directory lock, the tool checks receipt/hash,
   absence of artifacts and two fresh inventories around confirmation. It records
   the attestations, evidence hash, inventory hashes, operator UID and timestamp
   in `e2e-control/closed-4d51ffc5eb90ba5a.json`. The original receipt stays unknown;
   all locks and evidence remain. Decline, changed inventory, changed receipt,
   pending files or missing evidence leave the block in place.
5. Closure never resumes or repeats a phase. After a successful recorded decision,
   an operator may separately authorize a fresh fixture session in a new private
   fixture directory. The old directory remains evidence and cannot be replayed.
   A new directory before closure does not bypass the global fence. Removing or
   replacing that fence/lock is not a recovery operation.
