# Portfolio Deployment architecture

## Status and scope

Stages 01–04 provide registry/admission validation, immutable planning models and
offline Caddy previews. Stage 04 PR #4 is merged into `develop`. Stage 05 adds a
Python CLI engine foundation: deterministic Compose, bounded Docker-only discovery,
preflight, host locking, scoped execution, offline mocks and a separate opt-in
disposable smoke. It is not production-ready and real Docker smoke was not run.
Stage 05 PR #5 is merged into `develop`. Stage 06 adds offline readiness, SQLite
journal/LKG, constrained rollback and crash recovery analysis through the same
executor; PR #6 is merged into `develop`. Stage 07 adds a disposable Ubuntu VM
harness and real test-only readers/probes; real E2E remains not-performed.
The real registry/admission allowlist remain empty. No VPS integration is verified.

Python CLI, Docker Engine/Compose v2, GHCR, Git and Caddy are selected technologies.
There is no daemon, database server, Kubernetes or production deployment trigger.
The local durable journal uses standard-library SQLite schema v1. Ordinary live apply is unavailable: Docker-only
discovery cannot establish full Stage 03 registry/gateway evidence. See the
[Stage 05 contract](docs/docker-deployment-engine.md) for exact safe boundaries.

## Ownership and components

| Owner / component | Responsibility | Status |
| --- | --- | --- |
| [Portfolio Server Infrastructure](https://github.com/DanilFaritovich/portfolio-server-infrastructure) | Ubuntu VPS, SSH/users/access policy, Docker Engine, system baseline, firewall and host security | External ownership; integration Planned; operation not verified here |
| Service registry | Approved application identities, versioned multi-component contracts, references and storage declarations | Implemented offline; empty real registry |
| Desired state | Per-application manifests selecting component digests and references | Deeply immutable normalized model implemented offline |
| Validator | Bounded YAML, closed schemas, semantic/global checks and separate admission policy | Implemented offline |
| Planner | Pure desired/observed comparison using explicit local snapshots; reviewable plan only | Implemented offline |
| Compose engine | Deterministic renderer, bounded Docker discovery, ownership/preflight, lock and scoped executor | Implemented foundation with mocks; disposable smoke opt-in; full live observation/readiness integration pending |
| Caddy gateway | Canonical offline route/candidate rendering and opt-in local validation | Foundation implemented offline; Stage 07 adds VM-only runtime/activation; production TLS Planned |
| State/result journal | Intent/events, readiness evidence, atomic application LKG and recovery status under host lock | Implemented offline with SQLite; VM integration implemented in Stage 07; real acceptance not-performed |
| [Application repositories, e.g. project-router](https://github.com/DanilFaritovich/project-router) | Source, tests/CI, Docker builds, GHCR publishing and deployment contract | External ownership; integration Planned; deployability not verified |

Portfolio Deployment must not provision SSH/users, install Docker Engine or
duplicate infrastructure Ansible. It consumes a separately provisioned host and
documents deployment prerequisites. Host hardening remains an external invariant;
platform networking changes must respect it.

Applications build once and publish artifacts independently. This repository
does not rebuild their source on the VPS or take over their CI. A repository name
alone is not a deployment contract and does not establish that an image exists.

## Target topology — Planned

```text
Application repository -> tests/build -> GHCR image@sha256:digest
                                              |
Deployment Git -> approved desired state -> validate/plan -> reconciler
                                              |                |
                                              |          Docker Compose
                                              |                |
Internet -> Caddy gateway -> shared proxy network -> HTTP application
                                                     |
                                              private service network
                                                     |
                                           database / private dependency
```

Arrows describe intended trust/data flows. They do not imply installed resources.
Only Caddy intentionally publishes application HTTP(S) host ports (normally
80/443). Infrastructure-owned management access is a separate boundary.

## Publication and rollout flow — Planned

1. An application validates/tests its source, builds for the supported host
   platform, publishes to GHCR and exposes the resulting immutable digest plus
   its runtime contract. A readable commit tag may accompany the digest.
2. A trusted operator or approved automation proposes a desired-state change in
   this repository. Publication of an image does not itself authorize rollout.
3. Offline CI validates declarations and plans with fixtures; reviewers approve
   service/domain/image ownership and configuration prerequisites. Untrusted PR
   input is never executable deployment code.
4. A separately authorized execution selects the exact reviewed Git revision and
   digest. It verifies access, artifact availability and platform compatibility,
   then acquires a host-wide mutation lock and observes actual state.
5. The executor produces/rechecks the plan, pulls the digest and changes only the
   affected Compose project/services. Unchanged image/config yields no-op.
6. A newly created service must pass bounded internal readiness before public
   route activation. A route candidate is validated completely before reload.
   Image-only changes must not restart Caddy or rewrite unchanged routes.
7. Verify the affected path, record the outcome and last known-good state, then
   release the lock. Failure records partial state; it does not claim convergence.

Git approval, green CI, production-source eligibility and operator authorization
are separate gates. The future production source/release policy must be decided
before activation; an arbitrary task branch must not be deployed.

## Application registration contract — Implemented offline

One application = one manifest = one future independent Compose project. Each
component has one immutable GHCR image, explicit `linux/amd64` platform and an
HTTP readiness or worker image-healthcheck declaration. Each component will map
to one Compose service, without replica management in v1. Applications have at
most one public HTTP ingress; frontend-to-backend routing belongs to application
source. Internal applications may omit ingress entirely.

`registry/applications/<id>.yaml` uses `portfolio.deployment/v1`, `Application`.
[Application schema](schemas/application-v1.schema.json) and
[admission schema](schemas/admission-v1.schema.json) reject unknown fields.
[policy/admission.yaml](policy/admission.yaml) independently approves application
IDs, source repositories, per-component GHCR repositories, hostnames and platforms.
Policy changes require security review; branch/approver enforcement is deferred.

`scripts/validate_registry.py` bounds files/bytes/YAML depth/nodes, rejects unsafe
YAML constructs, validates version/structure, checks references, dependency cycles,
volume declarations and global IDs/hostnames/proxy aliases, then applies admission.
Its pure `validate_registry(root)` API returns validated documents or deterministic
errors; no writes, network, Docker, secret resolution or partial trusted result.
Stage 05 selects Python CLI execution while preserving this offline validator boundary.
See [manifest v1](docs/manifest-v1.md) for fields, limits, examples and the API.

Real registration still needs a confirmed compatible image and reviewed runtime
contract. OCI manifest/index existence, provenance and platform contents are not
verified offline. Config/secrets are symbolic references, never values. Named
volumes are declared and used explicitly; host mounts and runtime overrides are
forbidden. Omission or `enabled: false` authorizes no stop or data deletion.
Stage 05 renders Compose and implements scoped execution under explicit local test boundaries; production runtime reconciliation remains blocked.

## Desired state and observations — Implemented offline

Stage 03 data flow is `validate_registry(root)` → immutable `DesiredState` plus
explicit validated `ObservedState` → pure `build_reconciliation_plan` → immutable
`ReconciliationPlan`. Input adapters and CLI live in `scripts/planning_inputs.py`
and `scripts/plan_registry.py`; models/comparison live in `scripts/reconciliation.py`.
Canonical JSON strings and tuples provide deep immutability; accessors return
copies. The existing application/admission v1 contract is unchanged.

The versioned bounded [observed schema](schemas/observed-v1.schema.json) models
complete/trusted registry scope, absent/present/unknown, exact ownership,
minimal effective runtime, OCI pull-to-manifest identity and health observations.
Stage 03 loads explicit local JSON/native API fixtures. Stage 05 Docker discovery
remains separate and cannot claim full registry/gateway completeness. Incomplete evidence never means absence. The deterministic plan separates
component updates, routes, drift, health and pending lifecycle decisions. Storage
and mount changes block; matching reference IDs do not prove unchanged contents.
Blocking outranks degraded health, then changes, then no-op. Git revision changes
alone do not restart services. See [planning contract](docs/reconciliation-planning.md)
for actions, normalization, limitations, CLI/API and exit codes.

`make plan` requires explicit snapshot and full Git SHA. Plans always report
`executed: false` and `authorization: false`. Full registry/gateway state discovery,
runtime observation integration remains Planned; Stage 06 adds the journal. Recheck observations before apply and
serialize mutations; fixture trust flags cannot authorize production operations.

Stage 06 journals operation/component attempts, Git revision, previous/target
immutable image, configuration/scope hash, fixed outcomes and bounded readiness
evidence. SQLite does not supply live observations. The composite provider requires
fresh Docker, gateway, runtime and readiness source evidence without promoting
snapshot trust. Stage 07 supplies VM-only readers/probes; ordinary live apply remains
blocked. See [Stage 06](docs/readiness-recovery.md).

## Immutable artifact identity — Conservative Stage 05 foundation

Use `ghcr.io/<owner>/<image>@sha256:<64-hex-digest>` for execution and recovery.
Do not resolve `latest` during rollout or silently substitute a moving tag.
Keep Git revision, digest and runtime configuration revision discoverable together.
Verify that the digest is accessible from the target host and compatible with its
platform. The v1 contract permits either an OCI index or platform-specific manifest digest
with explicit target platform. Stage 05 only accepts preloaded manifest descriptors with corroborating Engine
evidence; index mapping and provenance verification remain future prerequisites.

A digest fixes content identity; it does not prove the artifact is trustworthy,
vulnerability-free, available indefinitely or compatible with stored data.
Allowlisted producers, retention and supply-chain verification need policy.

## Container and network isolation — Rendered; production integration Planned

Use stable service aliases and explicit project/resource ownership. Attach only
gateway-facing HTTP containers to the deployment-owned shared proxy network.
Keep databases, caches and other private dependencies on service-private networks;
do not publish their host ports. Avoid global teardown commands affecting other
Compose projects, gateway state or persistent data.

A shared Docker bridge is not a per-application security boundary: attached
containers can potentially reach each other. Review this residual lateral-access
risk; use per-service gateway networks or stronger isolation if requirements
justify it. Network membership must not imply trusted forwarded headers.

Prefer non-root applications, dropped capabilities, no privilege escalation,
read-only filesystems where compatible, resource limits and scoped writable
volumes. Ban privileged containers, host namespaces and Docker socket mounts in
applications and Caddy without a separately justified policy. Docker API access
for the executor is effectively host administration and must be tightly scoped.

## Caddy foundation — Implemented offline; HTTPS/reload Planned

Stage 04 data flow is validated DesiredState + ObservedState + matching
ReconciliationPlan → `scripts/gateway.py` → immutable GatewayCandidate. Frozen
GatewayRoute values identify owned host/component/port/derived Docker alias;
canonical JSON candidates separate desired, explicitly active current and complete
retained selection. Pure rendering emits a fixed deterministic Caddyfile; an empty
set is explicit no-routes, not an assumed valid empty config. Git SHA/image/reference
changes alone never alter routing. Stage 03 is reused without contract changes.

New/network routes are pending; route-only proposals report changes for review.
Both retain current selection until future gates exist. Disabled/removed routes
remain pending lifecycle decisions under Stage 03 blockers. Unknown, untrusted,
incomplete or conflicting route scope produces no full candidate; known full active
sets remain preserved under other plan blockers. No status authorizes reload.

`scripts/gateway_cli.py` and `make gateway-render` expose explicit local preview.
`scripts/gateway_validation.py` supports opt-in `caddy adapt` then stronger `caddy
validate`, fixed subprocess arguments/timeouts and temporary config/storage. Missing
Caddy reports not-performed; unit tests require no binary. No reload or discovery
exists. Stage 05 reuses Python for the separate Docker engine. See [Stage 04](docs/caddy-gateway.md) for
models, selection, CLI exits, trust limits and the future safe reload procedure.

The gateway owns public HTTP(S), validated host-to-upstream mappings and persistent
certificate/configuration storage. Registry data generates one canonical complete
configuration, avoiding competing manual route edits. Validate a temporary
candidate before supported hot reload; preserve previous working configuration
and unrelated routes on failure. Keep Caddy operational across application updates.

Admit only approved unique domains and explicit routing precedence. No implicit
wildcards/catch-all exposure. Define forwarded-header/client-IP trust, access-log
redaction and administrative endpoint isolation before implementation.

Public HTTPS requires authorized DNS, reachable ports and successful certificate
issuance in the real environment. Offline Caddy syntax validation and local test
TLS do not prove those conditions. Persist and protect certificate state; gateway
recovery must not depend on rebuilding an application. No domains or DNS records
are configured during Bootstrap.

## Health gates and failure handling — Implemented offline

Distinguish container started, process liveness, application readiness and external
route availability. Stage 06 checks HTTP 2xx without redirects or actual worker image-healthcheck
status, with configurable bounded deadlines and two consecutive successes. A healthy container alone does not prove public HTTPS works;
an unhealthy running container is not automatically restarted by Docker health
status. Gateway availability needs an independent check.

New services receive routes only after internal readiness. Existing service
updates need a reviewed strategy for readiness and traffic: in-place Compose
replacement can interrupt traffic. Zero downtime/blue-green is not guaranteed.
Preserve healthy unrelated routes and journal failed/partial convergence.

## Rollback and recovery boundaries — Implemented offline; live acceptance pending

SQLite v1 commits intent before each mutation and stores component transitions,
readiness evidence and application-wide atomic LKG pointers. Only proven stateless
image-only updates may receive one automatic rollback, with exact verified old
image availability, unchanged configuration/scope and explicit trusted exclusion of
migrations/side effects. Restored readiness and gateway evidence are required.
Stage 06 never changes gateway configuration. Unknown outcomes and unsafe rollback
require recovery; unfinished runs block further journaled operations. Crash recovery
only analyzes fresh actual state under host lock and never blindly resumes.

Image rollback cannot reverse database migrations, volume changes, external side
effects or a missing/garbage-collected artifact. Application contracts must state
migration order, backward compatibility, backup/restore responsibilities and
irreversible steps. Do not automate data deletion or promise automatic recovery
from arbitrary schema changes.

Separate application failure, gateway configuration failure, controller crash,
host reboot and host loss. Single-VPS operation has no high-availability guarantee.
Docker restart policy, journal recovery, retained certificates and backups require
testing. Lost SSH/host access and host rebuild belong to infrastructure recovery;
application data restore requires its own tested operator procedure. A deployment
journal is neither a backup nor a disaster-recovery system.

## Trust boundaries, secrets and permissions — Planned

- Application producer -> GHCR: trust approved repository/CI identity; digest
  selection does not automatically trust arbitrary publishers.
- PR author -> desired state: validate hostile input offline with no production
  credentials; never interpolate it into shell commands or privileged templates.
- Approved Git revision -> executor: require exact-revision checks, protected
  production source/environment and explicit authorization or approved policy.
- Executor -> Docker/host: treat Docker group/socket privileges as host-equivalent;
  minimize credentials, command surface and concurrent mutations.
- Gateway -> application: validate route ownership and upstream identity; define
  header trust explicitly and acknowledge shared-network lateral reachability.
- Runtime configuration -> service: Git stores references only. Keep real secrets
  server-side or in an approved secret store, with least-privilege read/pull scopes,
  redaction, rotation and access auditing. Private GHCR pulls need scoped read
  authorization decided later; do not request any production secret in Stage 01.

## Verification boundaries and implementation sequence

**Implemented:** `make fix` and `make check` validate repository text, local links,
YAML and installed standards without network, Docker or secrets. `make validate` checks the registry, and `make check` additionally
runs Stage 02–07 offline pytest suites. `make plan` performs explicit-input offline
planning. Dependencies are provisioned separately; clean setup may download PyYAML, jsonschema and pytest. External URLs are not contacted by checks.

**Planned offline CI (Stage 08):** use the same Makefile interface, pinned tools,
read-only checks and least-privilege permissions. No SSH, production endpoints,
DNS or production credentials. GitHub-hosted tool/source provisioning is distinct
from offline test execution. No workflow is installed through Stage 07.

**Implemented infrastructure, real E2E not-performed (Stage 07):** separate
Multipass Ubuntu VM, fixed local daemon identity, admitted immutable test images,
real private-network HTTP/Engine/Caddy observations, lifecycle-gated test-only
loopback activation and 15 scenarios. Separate opt-ins cover prepare, images,
run and cleanup. No VM or real Docker/Caddy was run during implementation. See
[local E2E](docs/local-e2e.md).

**Planned production verification (Stage 09):** separately approved host, access,
artifact, DNS/TLS, health and recovery checks. CI success alone does not authorize
production mutations. Never present local results as live acceptance evidence.

See the preliminary [roadmap](docs/roadmap.md). Stages 01–04 are implemented
offline; Stage 05 is an engine foundation with an unexecuted local smoke harness.
Stage 06 is implemented offline; Stage 07 infrastructure is implemented with live
acceptance not-performed; Stages 08–09 remain Planned. Production
source/trigger, configuration delivery, full live observation integration and
operator recovery/retention procedures remain open. See
[development instructions](AGENTS.md) for workflow boundaries.

## Stage 05 execution boundary

Validated DesiredApplication → immutable ComposeArtifact → matching Stage 03 plan
→ fixed host flock → freshly re-admitted desired plus refreshed observations →
preflight → scoped service update → immutable unverified outcome. Module details
and CLI commands are in the [engine contract](docs/docker-deployment-engine.md).

The project identity is `portfolio-<id>`, private internal bridge
`portfolio-<id>-private`, persistent volumes `portfolio-<id>-<volume>`. Only ingress
joins preprovisioned `portfolio-proxy` with `<id>-<component>` alias. Component
runtime labels exclude Git revision and sibling image choices. Config/secret refs
block until delivery is specified; existing storage/mount changes remain blocked.
No image pulls, builds, ordinary deletions or gateway operations are implemented.

`LocalStateProvider` deliberately returns incomplete Stage 03 observations because
Docker supplies no complete gateway/registry evidence. No production apply CLI
exists. Optional `previous_observed` supplies old pull references for fresh Docker
image inspection alongside desired images, without promoting snapshot trust;
Stage 06 defines the full composite evidence contract; Stage 07 supplies test-only
readers for the bounded disposable VM session. Deployment preview retains
proposed actions with separate eligibility and blocking reasons.
Only the separate disposable harness can perform real Docker operations,
with explicit canonical Unix socket, test namespace, ownership checks, preloaded
verified images and guarded session cleanup. Socket location is not proof of daemon
purpose. The local filesystem lock coordinates managed processes on one host,
not external Docker clients or runners on other hosts.

Mutation success is `applied-unverified`, never release success; timeout/disconnect
is `unknown`, requiring fresh discovery. Dependency creation does not prove
readiness, and partial execution is explicit. Stage 06 owns readiness, journal,
rollback and recovery; Stage 07 owns extended real E2E evidence. No live Docker
smoke or production operation was performed during Stage 05 implementation.

## Stage 06 lifecycle and durability boundary

`execute(..., lifecycle=DeploymentLifecycle(...))` reuses Stage 05 planning,
preflight, scoped adapter and host flock. No production entry point was added.
Within the same lock: commit prepared/applying intent → scoped mutation → fresh
composite discovery → pinned bounded readiness → rediscovery → component succeeded.
The application remains active until final component and required gateway checks
pass; only then do run success and all LKG pointers commit atomically. Partial
rollout or verified rollback does not imply release success.
An uninterrupted attempt may instead commit terminal run `recovered`, with fresh
`recovered-verified` component events, only after the whole application is verified
against its preceding LKG: exact images/runtime, ownership, bounded readiness and
a final coherent round corroborating every pinned container and required gateway
checks. This SQLite transaction checks integrity and record consistency, preserves
all previous LKG pointers and failed-target history, and allows a subsequent run
without declaring the failed deployment successful. Partial rollback, unknown
outcomes, crashes and missing evidence still block; no replay/resume or journal
clearing is provided. Stage 07 adds a VM-only composite provider; production
recovery confirmation remains closed.

The SQLite database and sidecars use private owned directories/files, NOFOLLOW
path walking, exact versioned schema/integrity/record validation, bounded metadata,
DELETE journaling and synchronous FULL. Active lock leases are required for journal
use. No raw logs/errors, HTTP bodies or effective runtime/secret values are stored.
SQLite durability depends on functioning local storage and fsync; retention,
backup, disk-loss recovery and real host acceptance remain operator prerequisites.
A journal is never complete/trusted Docker/Caddy evidence. See the
[Stage 06 contract](docs/readiness-recovery.md) for APIs, limits, state transitions,
analysis-only crash recovery and Stage 07–09 prerequisites.

## Stage 07 disposable VM integration boundary

`scripts/e2e/cli.py` is the host entry point. Shared `scripts/e2e/paths.py`
resolves checkout-independent absolute host defaults:
`~/.local/share/portfolio-deployment/e2e-state/` and
`~/.local/share/portfolio-deployment/e2e-fixtures/`. Optional state/fixture overrides
use the same validation. `Store` creates missing parents through no-follow pinned
descriptors with `0700` and retains the unchanged journal path validator; private
files use `0600`. No existing ancestor modes or owners are changed. Writable source
checkouts therefore need no permission repair. Path failures expose bounded safe
codes and guidance. Nonempty or unsafe legacy `.e2e-state`/`.e2e-fixtures` in the
source/invocation directory block unified commands even with overrides, pending
manual review and relocation; no automatic migration/reset/cleanup exists.
See [legacy transition](docs/local-e2e.md#legacy-state-transition).

Snap Multipass opens only bounded, verified copies in nonhidden
`~/PortfolioDeploymentE2E/<session>/<role>-<unique-operation>/` (`0700`/`0600`),
never private hidden storage or host temporary files. `staging.py` rejects unsafe
paths/owners and corroborates size/SHA-256 in host copies, Snap confinement and
guest artifacts. Staging files/transfer receipts are retained; GHCR credentials
remain exclusively in the publisher. `multipass.py` classifies transport,
cloud-init and identity failures with bounded allowlisted stderr evidence.
`control.py` adds a fixed private host-wide operation fence independent of output
path overrides. Independently reviewed absent-builder unknown preparation can
receive a separate immutable closure decision. Incident-specific `builder_recovery.py`
also closes failed preparation for session `b616980ef1c99192`: a read-only Running
guest identity/continuation review is recorded first, then independent operator
evidence and a separate manual stop are required. Closure accepts only Stopped,
rechecks inputs/inventory around confirmation and publishes a no-replace decision.
Running, missing/deleted or uncertain VMs remain blocked. The original unknown receipt,
locks, staging and evidence remain; no phase is resumed. Retained source/review
hashes are checked before new work, and the old directory/session cannot replay.
Stopped Multipass metadata does not expose guest machine-id: continuity across
stop explicitly relies on independent operator no-replacement attestation.
Phase approvals use separate
default-deny `y/N` prompts; cleanup separately approves the exact resource list.


`make e2e` orchestrates the existing
prepare/images/test lifecycle with a generated session, private pinned image-lock
snapshot, durable phase intents and a persistent whole-operation flock. Separate
interactive confirmations authorize VM creation, immutable pulls, Docker mutations
and Caddy activation. Host receipts pin VM machine/daemon identity; failed or unknown
sessions block another orchestration in that state directory without replay/reset.
`prepared_recovery.py` narrowly handles a lost terminal after successful image
pulls: `make e2e-interrupt-prepared` pins and verifies the idle controller with a
Linux pidfd and both flock owners, archives its receipt, then separately confirms
SIGINT through its existing handler. `make e2e-continue-prepared` requires the
matching stopped receipt, unchanged images/VM identity and pristine guest session
evidence. Separate run/activation approvals dispatch only the never-started run;
a new durable intent forbids replay. Original receipts and lock inodes remain;
only a complete real 15-scenario result closes the fence with retained evidence.
`e2e-close-failed-bootstrap` separately records an unsuccessful terminal bootstrap
only after matching host/guest reports, no guest controller or deployment evidence,
and an empty Caddy configuration. It retains all resources/evidence, permits a new
VM/session, and never replays the old run. The VM Caddy unit uses `Type=notify`
so service startup waits for Caddy's readiness signal before the first API read.
`e2e-close-failed-run` handles known terminal real failures without replay: it
matches host/guest reports and identity, checks absence of guest operations and
collects read-only SQLite attempt evidence under flock. Separate approvals stop
only the pinned disposable VM and record abandonment after Stopped metadata is
corroborated. Incomplete journals and disks remain unresolved evidence; immutable
closure decisions allow a new isolated session and retain checks on host evidence.
`e2e-report` reads local receipts, not live state. Missing fixtures are offered by
`make e2e`; `e2e-fixtures` prepares them separately with the same defaults and
confirmations. Verified GitHub origin supplies a lowercase owner. The reviewed
official Python source in `e2e/python-base.json` is resolved anonymously to one
direct linux/amd64 manifest; raw manifest/config digests, sizes, platform and
Python 3 metadata are checked and privately pinned before building. No mutable
tag is used after pinning. Explicit CLI `--plan --base` and raw-manifest import
remain offline. Separately confirmed execution adds disposable Ubuntu builder
and publisher VMs, with independent preparation/build/publication/public-verification
confirmations. Buildah runs daemonless in the builder; Skopeo preserves exact
exported direct Docker schema-2 manifests (including worker healthcheck) in GHCR.
Only the publisher receives its temporary publication credential via an operator
login inside that VM, attached directly to the `make e2e` terminal without
controller capture of authentication input/output. Anonymous exact manifest/config/platform verification gates
the fsynced atomic ready lock. Whole-operation flock, durable phase/role intents
and retained archives/VM evidence prevent retry/replay after uncertainty. No host
or E2E daemon participates in fixture creation. `make e2e` defaults to that ready
lock without rebuilding. Offline import alone does not prove registry availability
or producer provenance. Real fixture operations remain not-performed.

`scripts/e2e/vm.py` provisions only a new named Ubuntu 24.04 Multipass VM, transfers
allowlisted source without host mounts/credentials and records its source revision.
There is no host Docker fallback. Guest root checks session opt-in, private VM
marker, hostname/machine-id, virtualization, Ubuntu and pinned local Engine identity
before commands. The default public Caddy package unit is masked; the test unit
binds HTTP/admin exclusively to VM loopback. Applications publish no ports.

The isolated image contract hashes raw direct-manifest bytes and corroborates
Engine ID, RepoDigests and platform. Classic Engine config IDs must match the
locked manifest's config digest; Docker 29 containerd manifest IDs instead require
the exact locked descriptor digest, media type and raw-byte size. It neither substitutes
image IDs for manifest digests nor relaxes production admission; runtime refs and
OCI indexes remain blocked. Images are built/published separately, pulled only by
the explicit preparation command, and must be preloaded before execution.

The composite reader re-admits the separate fixture registry, checks complete
Engine inventory and effective runtime, independently reads active Caddy and probes
private HTTP/worker health, then closes with another inventory. Source trust is
scoped to that dedicated VM session; SQLite is not an observation source. Sampling
is not an atomic cross-system transaction or protection against a malicious root.

Stage 05 `execute` and Stage 06 lifecycle remain the only deployment engine. A
test-only lifecycle gateway hook rechecks pinned readiness, preserves other routes,
validates the full loopback JSON candidate, snapshots the prior config and issues
one explicit authorized Caddy load under the same fixed host lock. Unknown outcomes
stop acceptance; crash recovery stays analysis-only with no replay or journal reset.
The runtime identity re-canonicalizes after excluding images to preserve image-only
rollback eligibility across image-dependent input ordering. Old hashes are not
migrated or reinterpreted.

Fifteen real scenario definitions cover initial deployment, readiness/activation,
backend update, no-op, failed readiness/LKG, stateless rollback, persistent marker
preservation, invalid candidate, partial/unknown outcomes, SIGKILL windows, recovery,
foreign conflicts, host lock and unrelated resources. Offline tests use synthetic
transports and temporary SQLite and never claim real E2E acceptance. Cleanup is
manual opt-in, exact-owned containers/empty networks only; volumes, journal,
snapshots, unrelated resources and VM are retained. See [Stage 07](docs/local-e2e.md)
for commands, prerequisites, report semantics and remaining evidence limits.
