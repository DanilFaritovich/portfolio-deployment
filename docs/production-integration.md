# Stage 09.3 — Production integration operator contract

Stage 09.3 implements offline integration components. It does **not** authorize
host contact or enable production execution. The real application registry is
empty; ProjectRouter's template remains disabled. Stage 09.2 inspection is a
private diagnostic snapshot, never fresh planner state. No VDS, SSH, Docker
runtime, GHCR production download, DNS, TLS or certificate operation was performed.

[Stage 09.4B.4 installation/integration](production-installation.md) adds exact
bundle review, separate administrative installation, bounded local sources and
cache-only image preparation using the existing executor/release gates. Deployment
owns manager installation/storage/Caddy/networks; Server Infrastructure owns the
prepared host. Independent authentication/attestors, GitHub policy A/B and Caddy
provenance remain blockers; no production operation was performed.

## Components and trust boundaries

[Stage 09.4B.3](production-execution.md) now implements the separate one-shot
execution gate and protected installation contract over these ports and the same
Stage 05/06 engine. Historical preparation-only write restrictions below describe
the base read/test adapters; granted production adapters are separate and remain
unconfigured until independently reviewed installation. No production run occurred.

`Application CI → GHCR immutable digest → admitted desired state → Stage 03 plan
→ Stage 05 Compose executor → Stage 06 readiness/journal/LKG → managed Caddy`.
Public traffic is `Internet → Caddy 80/443 → portfolio-proxy → ingress component`.
Only ingress joins the shared network; every application gets its own internal
private network. Other components and persistent storage stay application scoped.

The implementation reuses `render_compose`, `discover`, `preflight_resources`,
`container_runtime`, `build_reconciliation_plan`, `CompositeStateProvider`,
`execute`, `DeploymentLifecycle`, readiness, fixed host flock, SQLite journal,
rollback policy and analysis-only crash recovery. There is one deployment engine
and one journal. Stage 07's VM harness remains unchanged and separately opt-in.
The new `scripts/production_integration/` package is deliberately independent of
Stage 09.2's `scripts/production/` SSH diagnostics package.

| Adapter | Implemented behavior | Remaining production prerequisite |
| --- | --- | --- |
| `docker.ProductionDockerAdapter` | Explicit local Unix endpoint; bounded inventory/inspect using Stage 05 methods; host and daemon identity checks; sanitized command environment | Separately approved Docker read access, actual identity and socket; mutations always blocked |
| `observations.ProductionStateReader` | Fresh source rounds; closed Stage 03 input; effective runtime/image/ownership corroboration; complete universe checks; double inventory/identity checks; CompositeStateProvider freshness gate | Independently trusted runtime delivery, active gateway and readiness source implementations configured for approved host; no source can be a journal or inspection report |
| `artifacts.verify_artifact` | Hash exact supplied bytes; index/platform manifest/config mapping, size/media/platform checks; immutable admitted repository; exact source/origin request; Engine config-ID binding | Stage 09.4B.2 collector installation with reviewed workflow/App/root pins and separately authorized live verification; no default credentials |
| `readiness.ProductionProbes` | Pin actual container ID, project/ownership and private IP/subnet; internal path/port; no redirects; bounded timeout; rediscover after probe | Approved bounded internal HTTP transport and reviewed private subnet; no HTTP client is configured by CLI |
| `runtime.CaddyRuntime` | Deterministic pinned-image Compose model; only 80/443 TCP; persistent data/config; no Docker socket; read-only managed configuration | Actual separately verified Caddy image/version, approved storage/permissions/budget and exclusive gateway ownership |
| `gateway_read.ManagedGatewayReadPort` | Exact managed active configuration, pinned config ID/version and ownership/storage attestation | Trusted active-config transport; disk config alone is insufficient; unknown configuration is rejected |
| `gateway.ProductionGateway` | Retained routes, readiness+lock gate, full candidate validation, unchanged-route no-op, durable previous config before compare-and-reload, post-read verification | Trusted validation/LKG/reload port; the supplied production read port rejects writes |
| `gateway.RehearsalLifecycle` | Exercises the future sequence through the existing executor and real temporary SQLite with fake ports | Inherits Stage 06's test-only session gate; cannot be used with production adapter |

These are executable contracts and injected ports, not a claim that all live
transports are installed. A trusted port is Python code inside the controller's
trust boundary, not a JSON field asserting `verified: true`. Saved snapshots and
fixture evidence cannot instantiate source trust or authorize a deployment.
Errors fail closed. Missing image inspection never proves absence or availability.

## Runtime security v1

Optional `components[].runtimeSecurity` is a closed versioned object; see
[the standalone schema](../schemas/runtime-security-v1.schema.json) and
[inactive ProjectRouter template](templates/project-router.yaml). The same closed
shape is embedded in desired and observed schemas. Existing manifests without
this field retain legacy semantics; production previews require explicit security
settings. Additional runtime requirements need a reviewed contract extension.

ProjectRouter selects read-only root, UID/GID `101:101`, all capabilities dropped,
no-new-privileges, `unless-stopped`, preserved image healthcheck and a 16 MiB tmpfs
at `/tmp` with `rw,noexec,nosuid,nodev,mode=1777`. No host ports, environment origin
substitution, privileged mode, socket or host mount is accepted. The allowed user
syntax excludes root UID/GID; tmpfs is bounded to 1–256 MiB and `/tmp` only. Other
apps may select writable root or another non-root user and supported restart
policy; the renderer does not hardcode ProjectRouter settings globally.

The renderer preserves image command/entrypoint/healthcheck and rejects unsupported
security fields. Preflight requires an actual image healthcheck for this contract,
rejects tmpfs/volume overlap and unknown resources in the target Compose project.
Effective runtime comparison inspects actual settings, mounts, networks, image ID
and defaults. Runtime changes affect Stage 03 diffs and Stage 06 runtime/LKG
identity; labels alone cannot establish a no-op or safe rollback.

Synthetic tests prove projection, bounds and rejection of contradictory inspected
runtime. They do **not** prove the published ProjectRouter image starts with these
settings. `read-only-image-compatibility-evidence-required` remains a blocker.
Before rollout, separately authorize an isolated runtime test of the exact verified
image: preserve image healthcheck, run as 101:101 with the rendered settings, verify
`/en/`, assets and 404 behavior, and demonstrate startup/request writes remain
within bounded `/tmp`. Do not relax read-only or add a writable mount if this fails;
review the producer image/contract instead. Stage 09.3 runs no such container.

## Artifact/source and application onboarding

A new application supplies a reviewed source repository, admitted GHCR repository,
exact digest, platform, component ports/readiness, explicit runtime security,
ingress/domain/origin, private dependencies and storage contract. The producer
owns source tests/build/publish; the manager owns networking and rollout. Runtime
config/secret references remain blocked until safe delivery is implemented.
ProjectRouter needs no secrets manager because it declares no runtime references.

Producer CI must publish from protected `main` only after required CI on the exact
producer SHA succeeds. The first manager rollout must also use protected `main`
with exact-SHA required checks and separate manual operation approval. A digest
proposal from `develop`, a tag, arbitrary SHA, OCI labels or a successful PR check
is insufficient. Neither repository is changed to enforce external branch rules
in this stage.

`verify_artifact` accepts bounded raw registry blobs supplied independently; it
never downloads them. SHA256 is calculated over original bytes, not reserialized
JSON. It distinguishes selected registry/index digest, platform manifest digest
and config ID, supports one index level and exactly one plain `linux/amd64`
descriptor, and binds the verified mapping to fresh Engine image inspection.
Unsupported platform variants/ambiguous descriptors are blockers. Layer availability
and actual image operation require the later approved Engine checks.

The injected source verifier must authenticate both repositories' branch rules and
protected-main ancestry, exact-SHA CI, producer publish workflow/builder identity,
provenance subject digest and build inputs. Its proof is bound to the complete
request and retained evidence identity. The repository intentionally supplies no
success-producing default verifier. Missing capability blocks validation. For
ProjectRouter, the expected source is `DanilFaritovich/project-router` and verified
build origin must equal `https://danil-miftakhov.dev`; runtime env cannot fix it.
Keep previous verified artifact bytes/evidence and immutable digest available for
rollback; GHCR retention is an operator/producer decision.

## Networking, Caddy ownership and storage

ProjectRouter remains exactly `portfolio-project-router` / `web`, alias
`project-router-web`, HTTP 8080, readiness `/en/`. The single route is
`danil-miftakhov.dev → project-router-web:8080`. Producer alias `project-router`,
`www`, wildcard, fallback and second hostnames are not added.

`ProxyContract` requires an approved existing network ID, subnet and gateway.
Observation validates exact ownership labels, bridge driver, local scope, IPAM,
options, internal/attachable/ingress/IPv6 flags. A familiar network name does not
permit adoption. Unknown containers/projects/resources or alias conflicts block
scoped operations; unrelated foreign resources are preserved, never renamed,
removed or pruned. Gateway/Nginx/Apache ownership or 80/443 listener conflicts must
be resolved by the operator, never by deleting another service.

`RuntimePaths` has no defaults: operator supplies separate non-overlapping absolute
paths for state, lock directory, journal, gateway configuration, certificate data
and Caddy config state, plus an explicit certificate-storage budget. Temporary,
E2E, proc/dev/sys and runtime directories are rejected. These path names are a
saved-input contract, not permission or proof of existing filesystem ownership.
No directory or network is provisioned by previews.

Before any operation, approve actual UID/GID, directory modes, symlink policy,
filesystem capacity, backups, retention and recovery owner. State, lock, journal
and gateway snapshots require private operator ownership (0700 directories/0600
files); preserve the fixed host lock inode. Certificate data contains private keys:
only the approved Caddy effective user and recovery operator may read it. Caddy
gets read-only `/etc/caddy` and persistent `/data` and `/config` mounts, independent
of application releases. Approve mount ownership against the selected image user;
do not make storage world-readable to solve permissions. Disk budget is declared,
not enforced as a filesystem quota in this stage.

Caddy's admin endpoint binds only container loopback `127.0.0.1:2019` and has no
published port. Only TCP 80/443 are projected; HTTP/3 UDP is not enabled. TLS is
Caddy's normal automation for exact admitted hostnames, with persistent certificate
storage. Offline rendering does not issue certificates or prove DNS/ACME reachability.
Pinned Caddy version/image and actual runtime ownership must be independently
attested; a syntactically valid version/digest is not verification.

Before a future route activation, validate the complete candidate with the pinned
trusted Caddy implementation in isolation; preserve the previous owned config
atomically/durably and compare current state immediately before reload. Image-only
updates return without reload or restart. Never overwrite foreign configuration.
On validation failure keep active/LKG configuration. On uncertain activation or
postcheck failure, preserve LKG and report recovery-required; do not blindly retry
or assume restoring a disk file restores active Caddy. Recovery requires fresh
active-state analysis and separately authorized supported restoration. The live
validation/reload/storage transports remain blocked pending Stage 09.4 review.

## Offline operator interfaces

Use the pinned environment from the root README:

```sh
make production-integration-check PYTHON=.venv/bin/python
make production-preflight PYTHON=.venv/bin/python PRODUCTION_CONTRACT=/absolute/reviewed-contract.json
make production-plan PYTHON=.venv/bin/python PRODUCTION_CONTRACT=/absolute/reviewed-contract.json OBSERVED=/absolute/saved-observed.json GIT_REVISION=<full-manager-sha>
```

The contract has exact keys `apiVersion: portfolio.production-runtime/v1`, `paths`
(the `RuntimePaths` fields above in snake_case), `proxy` (`subnet`, `gateway`,
`network_id`) and `caddy` (`image`, `version`). Unknown/missing fields are rejected.
There is deliberately no example synthetic production digest or default server
path. `production-preflight` and `production-plan` return exit 2/`blocked` even for
well-formed input: they perform no SSH/socket/registry/HTTP access, official Caddy
validation or mutation, and always emit `previewOnly`, `executed=false` and
`authorization=false`. Embedded Stage 03 plans remain saved-input previews.
`production-integration-check` is also included in the normal offline test suite.
No production-apply, deploy workflow, polling, webhook or background process exists.
Stage 09.2 `production-check` still proves only local diagnostic prerequisites.

## Lifecycle and Stage 09.4 boundary

The fake-adapter rehearsal follows source/operation prerequisites → host flock →
fresh complete discovery → exact plan comparison → existing journal intent →
scoped Compose update → bounded internal readiness → full Caddy validation →
readiness-gated activation → fresh postverification → atomic application LKG.
The lock spans the operation. Failed readiness prevents activation; partial or
unknown outcomes never report successful release. Journal errors and lock contention
retain the existing fail-closed behavior. Recovery never replays an interrupted run.

Rollback continues to use Stage 06's constrained policy: verified previous digest,
application-wide LKG/runtime identity, ownership, fresh complete evidence and
explicit exclusion of side effects. A first installation has no previous LKG.
Application rollback does not restore host state, persistent data, DNS or TLS.

Stage 09.4 must separately authorize and review fresh inspection, actual host and
Docker access/identity, private/proxy subnets and budgets, durable paths/permissions,
Caddy runtime and conflicts, source/provenance/origin evidence, exact images,
protected-main CI, approved operation scope and recovery procedure. It must configure
trusted production source/readiness/validation/storage transports and implement the
reviewed operation gate through the existing executor/lifecycle. Stage 09.3's
production adapters reject writes and inherited lifecycle rejects production
sessions; neither a fixture nor manual JSON approval can bypass that boundary.
First rollout, public TLS, external probes and controlled update/rollback acceptance
remain unperformed. No merge, release tag or automatic CD is authorized here.
Stage 09.6 will separately design producer-main digest PR proposals and policy-gated
CD after safe manual rollout/rollback is accepted.

## Stage 09.4A integration changes

[Production preparation](production-preparation.md) extends these ports without
replacing executor/lifecycle or enabling apply. SourceTrust now binds each source
to expected machine/daemon/source identities and an authenticated attestor;
ManagedGatewayReadPort also requires an independent attestor. A nonempty identity
and `verified=True` are insufficient. No attestor is configured from saved JSON.
Missing authenticated source/release/approval implementations remain blockers.

The Engine port executes only exact inventory/inspect commands, canonical local
socket and bounded timeouts; Docker permission errors have a redacted explicit
classification. It never escalates with sudo. InternalHTTPRead implements a
numeric private endpoint transport with an absolute deadline and bounded status
line. ProductionGateway's default lock verifier requires the canonical real
lease, and an exact empty managed gateway configuration can be observed and
preserved as a baseline. Foreign/absent Caddy cannot become an empty observation.
Image-only updates still do not reload Caddy.

RuntimePaths.lock now must be `/srv/portfolio/locks`, with the fixed `host.lock`
inode; the shared journal rejects forged active leases. Caddy bind sources have
`create_host_path: false`. The separate bootstrap proposal handles absent resources
without invented network IDs/routes, while existing-proxy reuse still requires
its exact independently approved identity and full contract. The handoff specifies
migration, storage budgets, TLS/first-release recovery and missing privileged
mechanism. Read-only CLI/CI and real registry/template activation boundaries remain
unchanged. See [ADR 0001](adr/0001-production-execution.md).

## ProjectRouter v1.0.0 acceptance preparation

A later scoped task prepared an exact-digest disabled
[candidate](candidates/project-router.yaml) outside the real registry and a
separate manual GitHub-hosted image acceptance workflow. Owner-reported release
and attestation results update the historical context above; manager-side live
byte/runtime verification and complete production admission remain pending.
The workflow was not run during implementation. See the
[acceptance handoff](project-router-acceptance.md) for exact release identity,
reviewed trigger, evidence limits and remaining production gates.

## Stage 09.4B.1 runtime acceptance update

ProjectRouter isolated acceptance [39837965499](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39837965499)
and manager main Offline CI [39839861288](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39839861288)
are now confirmed passed. This supersedes the pending isolated-acceptance status
in historical preparation sections, without granting production admission.

The [Caddy runtime acceptance guide](caddy-runtime-acceptance.md) records official
2.11.7 index/amd64 manifest/config mapping, image defaults, provenance limitations
and the separate disposable real-Docker workflow. It reuses the existing runtime
renderer and image contract with temporary paths and no published host ports.
Application OCI admission remains GHCR-only; a pure registry-independent mapper is
shared with the official gateway fixture verifier. No production attestor, validation/
reload transport, operator approval or write authority is installed by acceptance.

Caddy [real acceptance run 39844325743](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39844325743)
passed all 17 isolated cases and cleanup (Caddy 2.11.7 / Engine 28.0.4 / Compose
2.38.2). This supplies disposable runtime evidence only; no production gate is bypassed.

## Stage 09.4B.2 release/source trust update

[Release/source trust](production-release-source-trust.md) implements the missing
read-only collector port, separating authenticated GitHub API, public GHCR byte
transport, source policy, existing gh/Sigstore verification and authenticated facts.
It reuses the existing OCI mapper and acceptance signature command/selector; no
second verifier or deployment engine is added. Exact manager manifest/policy bytes
are checked at the selected protected-main SHA. CLI/CI still configure no collector,
production credential, write transport or approval issuer.

Offline API fixtures exercise success and fail-closed boundaries; real exact-release
collection was not run in this task. Caddy's hash-linked SLSA v0.2 statement has no
established independent publisher signature. Caddy admission remains blocked
pending independent cryptographic evidence or a separately reviewed security
policy. Neither this collector nor successful isolated acceptance authorizes rollout.
