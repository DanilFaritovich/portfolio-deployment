# Stage 05 — Docker Deployment Engine

Stage 05 implements a Python CLI foundation, deterministic Compose artifacts,
Docker-only discovery and a serialized component executor with offline mocks.
It is **not production-ready**. Production apply is deliberately unavailable:
Docker alone cannot supply the full Stage 03 observation contract. The only real
mutation entry point is the separately authorized disposable local smoke harness.
No live smoke, VPS operation, gateway activation or production deployment was
performed during implementation. Stage 04 PR #4 is merged into `develop`.

## Modules and interfaces

| Module | Contract |
| --- | --- |
| `scripts/compose.py` | `render_compose(DesiredApplication, test_session=None) -> ComposeArtifact`; `startup_order(application)` |
| `scripts/docker_adapter.py` | Local endpoint/scope checks, bounded subprocess runner, `LocalDockerAdapter`, offline `MockDockerAdapter`, optional official syntax validation |
| `scripts/docker_discovery.py` | `discover(adapter, references) -> DockerObservation`, resource/image models, effective runtime comparison, limited `LocalStateProvider` |
| `scripts/deployment.py` | Frozen `ExecutionOperation` / `ExecutionResult`, resource preflight, matching-plan preview, `host_lock`, `execute` |
| `scripts/deployment_cli.py` | Offline Compose and deployment previews; separate official Compose validation |
| `scripts/local_docker_smoke.py` | Test-only immutable image source, convergence checks and guarded session cleanup |

Use `build_desired_state(root, full_sha)` for complete Stage 02 validation and
admission. Direct immutable model construction is an internal trusted-value API,
as in Stage 03; it does not establish admission. Artifacts and resource facts are
frozen canonical JSON strings; accessors return copies. Execution adapters and
state providers are trusted code, never manifest plugins or user-provided commands.
No changes to application/admission schemas, Stage 03 or Stage 04 were needed.

Python reuses the existing validators/value models and keeps the execution surface
small. The invocation model is a finite CLI, without daemon, polling, webhook,
database, Kubernetes, Swarm or socket proxy. Durable storage remains undecided.

## Compose rendering and resource identity

One project is always `portfolio-<application-id>`; each component is one stable
service named exactly after the component. The explicit project name is passed
through both the model and `docker compose --project-name`. No current directory,
environment interpolation, Git SHA, timestamp or temporary filename enters the
artifact. JSON is also valid Compose input; YAML output uses sorted mappings.
Artifact SHA-256 covers canonical Compose JSON. Per-service runtime labels hash
only that normalized component, so backend image changes leave frontend unchanged.
Git SHA is operation metadata and cannot force container recreation.

Each service uses its exact immutable GHCR reference and `linux/amd64`. Supported
fields are image, platform, ownership/runtime labels, explicit networks, named
volume mounts and `depends_on` with `service_started`. There are no builds, host
ports, arbitrary Compose overrides, shell commands, command/entrypoint overrides,
host namespaces, privileged settings or socket mounts. Image defaults remain
intact; compatibility with non-root, capability, read-only filesystem and resource
limits needs an application contract before adding new hardening overrides.

HTTP readiness paths stay declarations. No in-image curl/wget/shell or invented
HEALTHCHECK is required. Worker image healthchecks are preserved; only actually
observed `State.Health.Status=healthy` can satisfy the limited dependency evidence
check. A running container does not prove readiness or release success.

The private bridge is `portfolio-<id>-private`, `internal: true`. This explicitly
restricts external connectivity for private-only services; applications needing
outbound connectivity require a reviewed networking extension. Only the selected
HTTP ingress joins external `portfolio-proxy`, with alias `<id>-<component>`.
Preview never creates networks. The shared bridge must already be provisioned
with platform labels `managed=true`, `role=proxy` under the namespace below. Shared
bridge access is not tenant isolation; peer access/header trust remains a concern.

Persistent volume `<name>` becomes `portfolio-<id>-<name>` using local named volumes
and long-form mounts with `nocopy: true`. No anonymous image-declared volumes are
allowed unless all their paths are covered by declared mounts. No host bindings,
automatic data initialization copies, adoption, migration or deletion is performed.
Existing volume driver/options and ownership must match. Stage 03 storage/mount
blockers remain authoritative. Image updates retain volume names and contents;
application compatibility and backups remain separate prerequisites.

## Ownership and discovery

The fixed label namespace is
`io.github.danilfaritovich.portfolio-deployment`:

- `.managed=true`, `.application=<id>` on project-owned resources;
- `.component=<name>` and `.runtime=<normalized component SHA-256>` on containers;
- `.role=proxy` on the separately managed external proxy bridge;
- `.test-session=<16 lowercase hex>` on disposable test resources only.

Cross-check project/service/network/volume identities with Docker Compose's
`com.docker.compose.*` labels and actual resource names. Names or labels alone are
not proof of ownership; labels are not cryptographic attestations. Unknown/foreign
resources, duplicate component containers, resource configuration conflicts,
foreign private-network/volume attachments and proxy alias collisions block.
Never relabel or adopt another application's resources.

The adapter inventories all containers, networks and volumes to detect conflicts
outside selected project labels. Commands have 20-second deadlines, a 1 MiB combined
stdout/stderr cap, at most 512 inventory entries per kind and 4 MiB per-kind
inspection budget. Inventory checks its 30-second deadline between inspections;
discovery checks its 60-second deadline between inventories/image reads. An
already-running bounded command can extend these deadlines. Exceeding bounds or
losing required information produces incomplete discovery, never absence.

Discovery records container existence/state, image/config identity, actual network
and volume attachments and ownership. Effective runtime comparison checks image
default command/entrypoint/environment/user/working directory/health settings,
networks/aliases/mounts and supported host configuration. Unsupported overrides
block instead of becoming no-op. Image references from both before and desired
state are required when verifying an existing update. Raw inspect data is internal;
it and Docker logs are never serialized into operation reports.

`DockerObservation.complete` covers **Docker inventory only**. Its
`registry_observed()` always returns `complete=false, trusted=false`.
`LocalStateProvider.refresh()` revalidates desired inputs and returns that limited
snapshot. Docker does not prove gateway routes, full registry completeness,
reference delivery or HTTP readiness. Consequently it cannot authorize ordinary
live execution. A future provider must supply independently verifiable, fresh,
complete Stage 03 evidence without weakening the schema or inventing health,
trust flags or OCI relationships. Fixtures are explicitly synthetic test evidence.

The optional keyword `LocalStateProvider(..., previous_observed=observed)` accepts
a validated immutable Stage 03 ObservedState as inspection hints. Refresh inspects
the union of desired images and present observed components' exact
`imageIdentity.pullReference` values, deduplicated. These hints never establish
trust or substitute an image/config ID for a pull reference. Missing previous
references/evidence and unverified old OCI mappings block updates. Stage 06 must
connect fresh full observations to this input and return independently verified
Stage 03 evidence under the execution lock; this provider still returns the
incomplete/untrusted snapshot above.

## OCI identity and preflight

OCI index digest, platform manifest digest and Docker image/config ID are distinct.
The conservative local adapter accepts an already-loaded image only when its exact
pull reference occurs in Engine `RepoDigests`, Engine `Descriptor.digest` equals
the selected digest, descriptor media type identifies an OCI/Docker **manifest**,
and inspected platform is `linux/amd64`. The resulting image ID is used only to
corroborate a container's actual image, never substituted for the manifest digest.
Missing Engine Descriptor support, index-only descriptors or unproven mapping block.
Stage 05 does not resolve registry indexes or download artifacts implicitly.

Preflight checks desired admission through the input/provider boundary, matching
Stage 03 plan, overall blocked/degraded status, desired enabled state, platform,
actual resource ownership/configuration, storage constraints, exact available
images, runtime drift, references, endpoint and authorized project scope. It runs
before the first component mutation. Existing state must match the plan's before
state; all no-op components must also have corroborated effective runtime.

Any declared `configRefs` / `secretRefs` blocks because delivery, bindings,
versions and effective content evidence are not defined. Matching IDs never proves
resolution. No empty values or secrets enter YAML, argv or output. Production
needs a reviewed delivery/versioning/rotation/redaction contract, scoped GHCR
access, producer authorization, provenance policy and artifact retention. Immutable
digests alone establish neither supply-chain trust nor production revision approval.
No network pull is implemented in this stage: preloading is a separate explicit
operator action, outside the executor. Compose always uses `--pull never`.

## Scoped execution, freshness and locking

`execute(desired, observed, plan, application, adapter, provider)` requires a
matching Stage 03 plan. `provider.refresh()` returns freshly re-admitted desired,
full ObservedState and DockerObservation. Under the lock, execution compares full
desired contents and revision, refreshes observations, recomputes the Stage 03
plan, then rechecks ownership and preflight. Any observed change invalidates the
preview, even if its diff would happen to remain equal. No stale fixture is an
execution authorization. The supplied SHA identifies requested state; it does not
verify Git checkout content or approve a production release.

The fixed host lock is `/tmp/portfolio-deployment-locks/host.lock`, protected by
nonblocking `flock`, owned private directory, NOFOLLOW and regular-file checks.
The inode is retained after release; stale file presence is not a lock. File
descriptor closure/process exit releases it. Contention fails immediately. All
managed runners on the host must use this path under the same operator identity;
a different user fails closed rather than silently choosing another lock.
Tests may inject an isolated directory. Never override the live lock location to
parallelize deployments. A filesystem lock does not serialize other Docker clients,
remote runners or actors with host privileges. Separate hosts/containers with
separate filesystems require a future coordination policy; external races remain.

Mutations use an explicit project/file/directory and only:

```text
docker --host unix:///explicit/socket compose --project-name PROJECT \
  --project-directory TEMP --env-file /dev/null --file TEMP/compose.json \
  up -d --no-deps --no-build --pull never SERVICE
```

Dependencies are topologically ordered. Existing dependencies must be observed
running with actual healthy image-healthcheck evidence. A component created during
the invocation is not assumed ready; dependent operations stop/pause until fresh
observation and the future readiness gate. Independent components may already have
applied; no transaction or rollback is promised. HTTP dependency readiness needs
Stage 06. A no-op emits no Docker mutation. Unrelated components/projects and Caddy
are never execution targets. In-place service replacement can interrupt traffic.
No ordinary executor deletion/down/remove-orphans/prune/build interface exists.

All subprocesses use argument arrays, `shell=False`, bounded execution and an
allowlisted environment. DOCKER_HOST/CONTEXT/TLS, inherited Compose settings,
credential directories and `.env` files cannot redirect execution. Real execution
requires an explicitly authorized disposable session and canonical Unix socket;
SSH/TCP/context endpoints and production project names are rejected. Compose v2
and an `amd64` Linux daemon are checked. A Unix socket's location and an opt-in
phrase cannot prove its purpose: the operator must verify the selected daemon is
local and disposable. Docker access remains effectively host-administrative.

## Outcomes and errors

Frozen results include application/component, desired revision, selected image,
planned action, affected project/service, fixed reason codes and next checks.
Every report has `readinessVerified=false` and `releaseSuccess=false`.

| Status | Meaning |
| --- | --- |
| `no-op` | Supported effective runtime matches; no mutation |
| `blocked` | Required preflight/dependency/freshness/lock evidence missing |
| `applied-unverified` | Compose command completed; readiness remains unverified |
| `failed` | Known nonzero command outcome; rediscover before retry |
| `unknown` | Timeout, connection interruption, bounded-output failure or unexpected adapter exception; fresh rediscovery required |

After a failed/unknown operation, remaining operations are blocked. Earlier
applied results remain explicit. Known command errors do not imply resources were
unchanged: Compose itself can partially apply before failing. Errors expose no
raw stdout/stderr, secret values or container logs. Process termination may prevent
any report; next invocation must rediscover. No persistent operation journal,
last-known-good state, automatic retry or rollback is implemented.

## Offline commands and official validation

The real registry/admission policy remain empty. Replace ROOT/APP/SHA/SNAPSHOT with
explicit valid inputs; no application is guessed. Test fixtures are not real images.

```text
make compose-render REGISTRY_ROOT=ROOT APPLICATION=APP GIT_REVISION=SHA COMPOSE_FORMAT=json
make deployment-plan REGISTRY_ROOT=ROOT APPLICATION=APP GIT_REVISION=SHA OBSERVED=SNAPSHOT
make compose-check
make compose-validate REGISTRY_ROOT=ROOT APPLICATION=APP GIT_REVISION=SHA
```

`compose-render` supports `COMPOSE_FORMAT=json|yaml`; its JSON is the Compose model
itself. `deployment-plan` JSON includes the unchanged Stage 03 plan, ordered
operations, project/hash and outstanding checks; `executed=false`,
`authorization=false`. It performs no discovery or mutations. CLI exits: 0 preview,
1 invalid inputs, 2 blocked, 3 degraded, 4 official validation not passed. GNU Make
maps failing recipes to its own error code. No deploy/apply target is installed.

Each operation retains its proposed `action` and separately reports `eligibility`
and `reasons`. Eligibility is `blocked` when the overall reconciliation is blocked
or degraded, including blockers outside the selected application; reasons contain
the blocking/degraded plan reasons and `reconciliation-not-admissible`. Otherwise
it is `requires-checks`, never execution authorization. A blocked proposed create
or update remains visible for review without implying executable eligibility.

`compose-check` and `make check` use offline tests only. `compose-validate` separately
runs installed Compose v2 `config -q`, with an intentionally nonexistent Docker
socket, isolated HOME/config and no environment-file interpolation. Missing Docker
or Compose v2 reports `not-performed`; syntax errors report `failed`. It downloads
nothing and starts no container. Syntax success proves neither image availability
nor readiness. See the official [Compose command interface](https://docs.docker.com/reference/cli/docker/compose/)
and [network specification](https://docs.docker.com/reference/compose-file/networks/).

## Opt-in disposable local smoke

The smoke target is implemented but was **not-performed** during development.
It is never invoked by `check` or `compose-check`. Prerequisites:

1. Separately prepare and verify a disposable local Linux/amd64 Docker daemon,
   canonical Unix socket, trusted Docker CLI and Compose v2.
2. Explicitly preload two different available immutable GHCR manifest images.
   They must have Engine Descriptor/RepoDigest evidence as above, keep their
   default process running without external config, need no published ports or
   privileged settings, and support a mount at `/pd-smoke-data`. Image-declared
   volumes outside that path block. No particular public image availability is
   claimed; no builds, random local IDs or automatic pulls are substituted.
3. Choose a previously unused 16-hex session token and inspect the scope. Use
   neither a production daemon nor a production project identifier.
4. Run the separate opt-in command yourself:

```text
make local-docker-smoke SMOKE_ENDPOINT=unix:///canonical/disposable/docker.sock \
  SMOKE_SESSION=0123456789abcdef SMOKE_IMAGE_A=ghcr.io/OWNER/IMAGE@sha256:DIGEST_A \
  SMOKE_IMAGE_B=ghcr.io/OWNER/IMAGE@sha256:DIGEST_B SMOKE_AUTHORIZE=disposable-local-docker
```

Uppercase placeholders must be replaced with real lowercase references. No test
image source bypass is exposed by normal admitted deployment paths. Synthetic
DesiredApplications in this harness are strictly test-only and never promoted to
Stage 03 production observations or the admission allowlist.

Resources are limited to `pd-smoke-<session>-sample` and
`pd-smoke-<session>-sentinel`, their internal bridges/volumes and labeled component
containers. No proxy bridge, Caddy, DNS, TLS, host ports or firewall modifications
are needed. Initial inventory must have no colliding names or session labels.
The harness creates both projects/components, updates backend only, checks fresh
container identities for frontend, sentinel and unrelated containers, verifies
stable volume identity, absence of published ports, observed-runtime no-op without
mutation and a deliberate missing-container inspection failure. Volume identity
is checked; application-level data integrity/recovery testing belongs to Stage 07.

Before cleanup, verify the complete target set against session and Compose
ownership, expected names and pre-session inventory. Recheck before each deletion.
Only this session's newly created containers are stopped/removed, then its newly
created networks/volumes removed. Existing volumes are never cleanup targets.
This separate harness lifecycle is not part of the normal executor. No force or
prune commands exist. Ownership conflicts/incomplete observations stop cleanup and
report the test namespace for manual review. The lock cannot prevent third-party
clients from racing cleanup; disposable daemon isolation is preferred.

Missing authorization, endpoint, images or prerequisites returns `not-performed`;
failed/unknown execution or cleanup conflict is never reported as passed. Output
is compact JSON with no Docker environment contents or raw command logs.

## Remaining Stage 06–09 decisions

Stage 06 must provide real bounded HTTP/image-healthcheck readiness, full fresh
registry/gateway observation integration, durable attempted/last-known-good journal,
crash recovery and explicit rollback compatibility rules. Config delivery,
production-source approval, index-to-platform evidence and host coordination need
review before ordinary live execution can be enabled. Avoid pretending fixtures
can substitute for these contracts.

Stage 07 adds real isolated end-to-end failure/recovery, persistence and gateway
integration tests. Stage 08 owns offline GitHub CI and protected automation; Stage 09
owns separately authorized production integration. Production SSH/VPS, Docker
installation, migrations, secret delivery, Caddy activation/reload, DNS/TLS,
monitoring, blue-green and multiple replicas are outside Stage 05. No zero-downtime,
rollback or production release claim follows from offline tests or Compose exit 0.
