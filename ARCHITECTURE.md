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
harness and real test-only readers/probes; all 15 real local VM E2E scenarios
passed on 2026-10-21. This verifies only the disposable test integration.
Stage 08 offline CI is merged into `develop`. Stage 09.1 adds offline production
contracts/security and narrow ProjectRouter admission; the real registry remains
empty pending a verified origin-bound digest. Stage 09.2 adds an offline-tested
read-only inspection layer; no real VDS connection or inspection is verified.
Stage 09.3 adds offline-tested production integration ports, runtime security and
a fake-adapter lifecycle rehearsal. Production write transports and source/operation
authorization remain blocked. Stage 09.4A adds offline first-host bootstrap,
explicit source trust, canonical production lock/storage, release evidence and
operator candidate/approval review contracts. Stage 09.4B remains the separately
authorized first rollout. Stage 09.4B.3 implements a one-shot execution mechanism
with durable one-time authorization over the same engine, plus protected installation
artifacts; no production installation or live execution is configured/performed.

Stage 09.4B.4 adds [operator installation/integration](docs/production-installation.md).
Deployment owns its initial installation and manager resources; Server Infrastructure
supplies only the prepared/hardened host. An unprivileged exact-Git-object bundle
builder and independent ProtectedSource-based manager/tree/dependency reviewer
feed a separately authenticated administrative bootstrap. The standalone stdlib
helper never imports bundle code, accepts no argv, rejects writable checkouts,
provisions only protected fixed paths and preserves existing data/lock inode.
It installs the existing launcher/unit without starting or enabling anything.
The administrative authentication binding is independently installed and blocks
by default; no release or configuration file grants its authority.

Concrete fixed local machine/kernel/storage and read-only SQLite sources emit
bounded fresh facts for existing HostContracts/SourceTrust composition; they do
not attest their own payloads or manufacture complete planner evidence. Independent
runtime/gateway/readiness/host/bootstrap attestors remain missing. A local empty
Caddy validation binding reuses existing ManagedBootstrapPort/BootstrapExecution;
provenance and exact-image runtime acceptance remain mandatory external gates.

`prepare-project-router-image` is a separate cache-only OperationCandidate scope
in ProductionExecutor, using the same full ReleaseVerifier, signed operator approval,
canonical lease and durable shared SQLite nonce/candidate ledger. Exact immutable
GHCR OCI evidence binds the candidate; only approved linux/amd64 ProjectRouter
references can be pulled. Complete cache inventory distinguishes no-op/absence;
two fresh Engine inspections verify digest/config/platform afterward. Consumed
ledger payload records cache intent before mutation; failures fence all scopes.
This scope does not create application/gateway LKG or authorize first-bootstrap or
first-project-router-rollout. Caddy acquisition remains outside this scope.
No production binding, administrative channel, VDS operation or CD is enabled.

Stage 09.4B.7 adds a separate [trusted Caddy image producer](docs/trusted-caddy-image.md):
the Sigstore-authenticated Caddy 2.11.7 static release is packaged over an authenticated
digest-pinned Distroless rootfs, without compilation or package installation.
Read-only PR builds precede protected-main GHCR publication, isolated exact-digest
acceptance and signed SLSA/input attestations. A separate CaddyReleaseCollector reuses
ProtectedSource, OCI mapping and pinned gh/Sigstore, binding source SHA, signer/builder,
inputs and successful publication attempt. CaddyRuntime additionally accepts only
the fixed producer GHCR repository by digest; runtime hardening is unchanged.
Publication waits boundedly for exact main-push Offline CI, then rechecks full
source policy with a separately supplied repository-scoped read-only credential;
the workflow token cannot supply Administration:read branch-protection evidence.
First main publication, signed digest, real exact-image acceptance and installed
production attestation remain pending; no Caddy acquisition/rollout is authorized.

Python CLI, Docker Engine/Compose (stable plugin versions >= 2, with separate CLI/configuration gates),
GHCR, Git and Caddy are selected technologies.
There is no daemon, database server, Kubernetes or production deployment trigger.

Stage 09.4B.3's [execution contract](docs/production-execution.md) separates sealed
root-owned installation, authenticated read-only collection/attestation, deterministic
candidate creation and independent signed operator approval. ProductionExecutor
revalidates the exact candidate under `/srv/portfolio/locks/host.lock`, consumes
authorization durably, then passes that issued lease to the existing Stage 05
execute and Stage 06 lifecycle. ProductionLifecycle reuses readiness/rollback and
gateway sequencing; base disposable gates remain intact. The shared fixed Compose
transport performs no build or implicit pull. Fixed manager-only bootstrap actions
and exact-container Caddy validation/reload ports require installed attestors.

SQLite schema v2 adds an irreversible nonce/candidate ledger in the same journal,
with transactional migration preserving v1 events/LKG. Consumption precedes intent;
unfinished consumption fences new work even if a crash left no engine intent.
Success, blocked and recovery-required results retain spent identities; unknown
outcomes have no replay/reset API. Bootstrap authorization/LKG must match the actual
host/daemon before the independently approved first application rollout. Installation
launcher/unit specifications live in `installation/`; shipped bindings always block.
No issuer, production attestor, code installation or live release verification is
configured by CLI/CI or a saved report. Source/Caddy provenance gates are unchanged.
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
| Compose engine | Deterministic renderer, bounded Docker discovery, ownership/preflight, lock and scoped executor | Implemented foundation with mocks; disposable smoke opt-in; full production observation/readiness integration pending |
| Caddy gateway | Canonical offline route/candidate rendering and opt-in local validation | Foundation implemented offline; Stage 07 adds VM-only runtime/activation; production TLS Planned |
| State/result journal | Intent/events, readiness evidence, atomic application LKG and recovery status under host lock | Implemented with SQLite; Stage 07 disposable VM integration accepted by 15 real scenarios |
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
are separate gates. Stage 09.1 selects manual deployment from protected `main` in producer and manager;
protection enforcement and exact-SHA CI evidence are prerequisites. Arbitrary task
branches, develop artifacts and unapproved tags must not be deployed.

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

Stage 09.1 [production contracts/security](docs/production-contracts.md) adds an
optional closed admission `staticSite` contract: one HTTP component with exact
port/readiness, ingress matching expected HTTPS build origin, stateless storage
and no runtime refs/dependencies/mounts. It cannot prove artifact bytes/provenance.
ProjectRouter eligibility binds `DanilFaritovich/project-router`, its exact GHCR
repository, `linux/amd64`, `danil-miftakhov.dev`, port 8080 and `/en/`. Canonical
project/service/alias remain `portfolio-project-router` / `web` /
`project-router-web`, on external `portfolio-proxy`; the producer's bare alias is
translated at this boundary. The disabled template stays outside the registry
until reviewed digest substitution. No production executor or trigger is added.
Stage 09.3 adds an explicit per-component runtime-security projection; compatibility
of the exact published producer image with that projection still requires evidence;
complete observations, protected-source exact-SHA CI, verified OCI/origin evidence
and manual first-deployment authorization are independent gates before mutation.

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

## Rollback and recovery boundaries — Local VM E2E accepted; production acceptance pending

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

## Trust boundaries, secrets and permissions — Policy defined offline; enforcement pending

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

**Implemented offline CI (Stage 08):** PRs into `develop`/`main` run `make check`
and `make workflow-check` with pinned Python/dependencies/actions and checked
actionlint, pip caching and `contents: read` only. Source credentials are not
persisted. Tool/source provisioning needs network; validation is offline and
read-only. No VM/E2E, Docker, publication or deployment runs. Required branch
checks remain an administrator setting. See [Stage 08](docs/stage-08-automation.md).

The VM-only gateway now requires retained-route reachability before `/load`; the
replacement target uses caller readiness instead of its stale old endpoint. All
routes are verified after load. Successful VM completion is a documented manual
export/integrity-review procedure with separate stop/delete approvals, not a new
recovery/deletion API. No VM was stopped/deleted; failed-session evidence and
recovery fences remain unchanged.

**Implemented and accepted in a disposable local VM (Stage 07):** separate
Multipass Ubuntu VM, fixed local daemon identity, admitted immutable test images,
real private-network HTTP/Engine/Caddy observations, lifecycle-gated test-only
loopback activation and 15 scenarios. Separate opt-ins cover prepare, images,
run and cleanup. All 15 real Docker/Caddy scenarios (**15/15**) passed in session
`4963fe050c4567f2` on 2026-10-21, using source revision
`35af4761ee80d32e657174e08dd086a12022562d`. See
[local E2E](docs/local-e2e.md).

**Planned production verification (Stage 09):** separately approved host, access,
artifact, DNS/TLS, health and recovery checks. CI success alone does not authorize
production mutations. Never present local results as production acceptance evidence.

See the preliminary [roadmap](docs/roadmap.md). Stages 01–04 are implemented
offline; Stage 05 is an engine foundation with an unexecuted local smoke harness.
Stage 06 is implemented offline; Stage 07 infrastructure passed real disposable
VM acceptance; Stage 08 adds offline CI and Stage 09.1 defines production contracts/security.
Stage 09.2 is implemented offline; Stages 09.3–09.5 remain Planned. Protected-source enforcement, configuration delivery, full live observation integration and
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

Acceptance evidence is retained outside Git: host receipt
`~/.local/share/portfolio-deployment/e2e-state-agent.uZQWee44/4963fe050c4567f2.json`
records `complete/passed`, `mode=real`, `realE2E=performed` and all 15 results.
The accepted implementation also passed 779 offline tests. Production deployment
was not performed; Stage 08 adds offline CI and Stage 09.1 defines offline contracts/security;
Stage 09.2 is implemented offline; Stages 09.3–09.5 remain Planned.
See [acceptance and failed-attempt history](docs/local-e2e.md#verified-local-acceptance).
The historical acceptance recorded reports, baseline, gateway snapshots and intentional crash/unknown
journals; current VM existence is not asserted here; recovery analysis confirms no automatic completion or replay. Failed
earlier attempts retain independent closure evidence and original journals.

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
or producer provenance. Real fixture session `5b780834b80ffe9e` completed build,
publication and anonymous verification of all six digests; its retained
`e2e-fixtures-agent.FWWSgbBJ/execution.json` and `artifacts/lock.json` pin them.

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

## Stage 09.2 read-only inspection boundary

`scripts/production/inventory.py` is a small static Ansible inventory adapter,
reusing the bounded YAML loader from `validate_registry`. It selects only the
explicit managed host/port/user/key from the sibling infrastructure inventory.
No infrastructure controller, playbook or alternate identity system is imported.
`transport.py` performs local trust/prerequisite checks and bounds OpenSSH subprocess
pipes/timeouts. Only `production-inspect` constructs it after terminal confirmation;
`production-check` and `production-report` are offline. SSH config/proxies/forwarding,
password fallback, trust changes and connection sharing are disabled. The existing
selected identity can use the invoking shell's unlocked agent.

`probes.py` owns static read-only commands and closed normalization. `inspection.py`
collects independent system/Docker/network evidence, classifies transport failures,
reserved resources/listeners, label ownership claims and subnet overlap.
`report.py` validates the versioned closed JSON schema and atomically stores only
the latest private normalized report outside Git (0700 directory/0600 file).
`cli.py` exposes the three Make commands. No raw stderr, full Docker inspect,
container secrets, private keys or environment are persisted. No mutating transport
or executor dependency is introduced. Reports cannot be converted into a trusted
complete ObservedState and never relax existing planner/lifecycle safety gates.

The baseline is Ubuntu 24.04 / x86_64 and the rootful local Docker socket; other
scopes, unprivileged firewall gaps and external DNS/TLS/GHCR remain explicit unknowns
or prerequisites. Existing SSH trust is not proof of its initial independent
verification. Actual diagnostics require separate authorization and report review
before 09.3. See the [operator guide](docs/production-inspection.md) for exact
bounds, permissions, trust handoff, statuses and retained production blockers.

## Stage 09.3 production integration boundary

[Operator contract and architecture](docs/production-integration.md) describes the
new `scripts/production_integration/` ports, independent of Stage 09.2 diagnostics.
The local Engine read adapter reuses Stage 05 bounded commands and verifies approved
host/daemon identity. Fresh runtime/gateway/readiness ports compose through
`CompositeStateProvider`, corroborate actual images/resources/runtime and repeat
inventory/identity checks. Incomplete, stale, conflicting or unverified evidence
cannot become complete state. There is no source promotion from inspection or LKG.

`portfolio.runtime/v1` is a closed optional per-component security contract shared
by desired/observed schemas. Compose renders user, root filesystem, bounded tmpfs,
capability drop, no-new-privileges and restart policy deterministically, preserving
image healthcheck. Effective runtime comparison and existing plan/runtime identity
include these settings. Legacy fixtures remain compatible; production previews
require explicit security. ProjectRouter's inactive template selects 101:101,
read-only root and bounded `/tmp`; actual-image compatibility remains unverified.

OCI verification hashes supplied registry bytes, maps index to unique linux/amd64
manifest and config ID, and binds fresh Engine inspection. Source/protected-main,
exact-SHA CI/publish, provenance and build-origin verification is an injected trusted
port with no success-producing default. Missing evidence blocks operation. Caddy
runtime Compose is pinned and separate from applications, exposes TCP 80/443 only,
keeps admin on container loopback and persists certificate/config storage under
explicit operator paths. Exact proxy ownership, network ID and IPAM are required.

The production gateway controller retains unrelated routes, validates full candidates,
preserves previous config before compare/reload and skips reload for identical
routing. Its live write/validation/LKG transports are not configured. The rehearsal
reuses `execute` and `DeploymentLifecycle`, host flock, readiness, SQLite journal,
atomic application LKG and constrained rollback/recovery with fake ports. Inherited
test-only lifecycle and production write blockers remain intact. No second engine,
journal, live apply CLI or deployment trigger exists. 1055 offline tests passed;
no production runtime, SSH, GHCR download or TLS operation was performed.

`production-preflight` and `production-plan` consume explicit saved inputs and
always return blocked previews without authorization. Stage 09.4 must supply and
review actual identities/permissions, paths/budgets/subnets, gateway ownership,
protected-main source/artifact/origin evidence, trusted live transports and operation
approval. Stage 09.6 automated digest proposal/CD remains separately planned after
manual rollout/rollback acceptance.

## Stage 09.4A preparation boundary

[ADR 0001](docs/adr/0001-production-execution.md) recommends a host-local one-shot
execution process behind a separately provisioned privileged boundary. Inspection,
planning and mutation authority stay separate; existing administrative `sudo -n`
reads do not grant deployment rights. No Docker group/socket/sudoers change or
privileged implementation is supplied. The managed inventory remains authoritative
for future approved host access. No VDS contact occurred in this stage.

Production sources require explicit expected host/daemon/source bindings and an
installed authenticated attestor in addition to fresh complete sampling rounds.
The read-only Engine adapter admits fixed argv, canonical local socket and bounded
execution. A numeric private HTTP status transport is implemented without DNS,
redirects, body logging or unbounded reads. Active gateway reads require their own
attestor; exact empty managed config is valid, absent/foreign gateway is not.

`bootstrap.py` plans manager infrastructure from independently attested empty
state or a fully verified existing proxy. It rejects ownership/IPAM/listener and
storage conflicts and separates manager bootstrap from readiness-gated application
route activation. Proposals bind the observed baseline and never authorize writes.
An absent prior application LKG explicitly means no release rollback is available.

The canonical production lock is `/srv/portfolio/locks/host.lock`, acquired through
the existing Stage 05 flock primitive after protected parent/ownership/mode checks.
Journal operations require an issued live lease with a matching inode;
ProductionGateway defaults to canonical-lease verification. Injectable disposable
test locks remain separate; production fallback/migration is forbidden. The
[operator guide](docs/production-preparation.md) defines storage budgets, private
paths, backups, migration and first-release recovery limitations.

ReleaseVerifier validates protected-main/check/publication/provenance/origin and
candidate relationships only through an authenticated collector; unconfigured
collectors block. The existing OCI byte verifier remains the artifact mapper.
OperationCandidate binds host/daemon, both SHAs, image and manifest/policy/plan/
gateway/runtime/network/storage/recovery hashes. Approval review requires an
independent issuer and bounded window; it yields no executor capability. Issuance,
nonce consumption and real dispatch remain 09.4B work. CLI/CI configure no trust
issuer or production write port. Compose version acceptance and synthetic tests
do not prove actual image/runtime compatibility, DNS, ACME or production readiness.

## Universal isolated application acceptance

The [dispatch-only GitHub-hosted workflow](.github/workflows/application-acceptance.yml)
selects a reviewed Application by ID on protected develop/main. One YAML contains
manifest v1 plus optional versioned `release` expectations; separate admission
`acceptance` constrains signer workflows/refs, predicates and exact runtime security.
`scripts/acceptance_contract.py` reuses complete registry/schema/admission validation
and checks candidates together with existing registry entries for global collisions.
`scripts/application_acceptance.py` reuses OCI byte verification, Engine binding,
Compose test-session rendering, shared readiness gates and runtime inspection.
Adding supported applications requires declarations and security review, with no
per-application runner/workflow or engine change. No daemon/production trigger is added.

All image/source signatures are verified before pulls. Session-isolated Compose
startup follows readiness-gated dependencies; checks cover HTTP/worker readiness,
internal DNS/TCP, PID security, mounts/ports, restart and owned-resource cleanup.
Persistent storage requires explicit disposable test-volume consent and collision-free
preflight. Unsupported secrets/config and unavailable capabilities fail closed.
Producer business/UI/PDF/SEO tests remain in application CI. ProjectRouter's exact
v1.0.0 candidate stays disabled outside the registry with signed origin expectations.

The bounded sanitized report always has `productionAdmission: false` and is not
accepted by the separate full production source/release verifier or operation gate.
The [runtime contract audit](docs/runtime-contract-audit.md) documents the shared
`scripts/runtime_contract.py` verifier behind discovery and acceptance boolean
facades. One immutable structured result drives pass/fail and fixed group/field
codes without copying inspect values. Typed normalizations distinguish image
runtime defaults and Docker-managed metadata, including legacy HostConfig tmpfs
that is absent from inspect Mounts. Acceptance classifies container state before
runtime configuration. A separate opt-in `make runtime-integration-test` and
GitHub-hosted PR job exercise real Compose/Engine with a local scratch fixture,
without registry access or published ports. Nineteen checks passed on disposable
Engine 29.1.3 / Compose 2.40.3; this does not establish ProjectRouter acceptance.

Runtime diagnostics distinguish preflight, network creation, Compose startup,
discovery/inspect, ownership/identity, runtime contract, container state, Docker
health and HTTP readiness using fixed codes, allowlisted statuses and bounded
counters. The shared readiness adapter's `ReadinessContractError` propagates fatal
contract violations immediately; transient HTTP transport failures remain bounded
readiness attempts. Cleanup preserves the original runtime diagnosis even on failure;
no exception text, logs, response bodies or full inspect data enter these diagnostics.
Offline CI validates declarations and mocks; no real universal acceptance or
production operation was run in this task. See [adding applications](docs/application-acceptance.md)
and [ProjectRouter's remaining blockers](docs/project-router-acceptance.md).

## Stage 09.4B.1 Caddy runtime acceptance boundary

[Isolated Caddy acceptance](docs/caddy-runtime-acceptance.md) adds a dedicated
opt-in GitHub-hosted PR harness over `CaddyRuntime.render()`, the existing image
contract, shared OCI byte mapper/Engine binding and session-scoped Docker helpers.
`production_integration/caddy_runtime.py` compares gateway-specific Engine fields
with fixed diagnostics; it grants no active-gateway attestation or production
write capability. The application runtime verifier/admission remain unchanged.
No second gateway controller, Compose renderer or deployment engine is introduced.

The official Caddy 2.11.7 amd64 index/manifest/config bytes are pinned in separate
acceptance fixtures. Raw bytes/provenance statements establish content linkage;
cryptographic build-origin verification remains a production blocker. The harness
substitutes temporary bind paths/project/network names, removes host publishing,
uses internal networking and runs an exact HTTP-only fixture with loopback admin.
Official validation also checks the existing bootstrap and full generated candidate
without activation. Process capabilities, read-only paths, restart/persistence,
peer reachability/admin denial, negative create-only configurations and cleanup
after partial failure have offline coverage and passed 17/17 real Docker cases
in run 39844325743 (Caddy 2.11.7 / Engine 28.0.4 / Compose 2.38.2), including cleanup.
No fixture receipt can satisfy production trust, release verification or approval.

Confirmed prior evidence: ProjectRouter acceptance run 39837965499 and main Offline
CI 39839861288 succeeded; see the [acceptance guide](docs/project-router-acceptance.md).
Caddy real-runtime results are tracked separately in its acceptance guide. Production
remains unperformed; Stage 09.4B privileged execution, authenticated observations,
source/provenance, actual candidate/DNS/TLS/storage and explicit rollout approval
remain required. ProductionGateway and ManagedGatewayReadPort write blockers stay intact.

## Stage 09.4B.2 release/source trust boundary

The Caddy reusable publisher authenticates protected-source reads with a GitHub
App installation token minted immediately before its guard, scoped to the current
repository with Administration:write and Actions/Checks/Contents:read permissions.
GitHub requires Rulesets write access to expose `bypass_actors`; the verifier
still issues only GET requests. This grant could mutate repository settings if
misused, so reviewed protected-branch code and step-local credential isolation
are security boundaries. Repository
Client ID variable and explicitly passed private-key secret are confined to the
credential gate and official SHA-pinned token action. Only the guard receives the
token via step-local `GH_TOKEN`; action post-job revocation remains enabled.
Policy B, exact-SHA Offline CI and trusted workflow/check App identity are unchanged.
Build, registry publication, signing and provenance receive no App credential.

GitHub credentials are bounded opaque HTTP-header values, including stateless
`ghs_APPID_JWT` tokens; no JWT contents are decoded or locally trusted. Guard
failures expose only exact allowlisted reason codes, with fixed redacted codes
for incomplete responses and unexpected failures. Unreadable ruleset bypass
actors remain a blocker even with Administration:write; no policy fallback is added.

`caddy-source-smoke.yml` prepares real read-only App integration only on a push
to protected `develop`, with the same repository scope, permission grants and
post-job token revocation as publication. `caddy_source_smoke.py` verifies current
protected main through the shared full source verifier, including exact-SHA push
CI, App/check-suite identity and repeated head/protection reads. It neither builds
nor publishes images and has no Docker step. Release PRs into main add a read-only
Actions-metadata gate inside required Offline checks: the exact protected develop
head must already have a successful push smoke. PR code receives no App secret.
This workflow must first be reviewed and explicitly merged into develop before
the real App smoke can run; local/offline success does not establish live App API
compatibility. This task does not authorize that merge or a publication retry.

The [read-only release collector](docs/production-release-source-trust.md) implements
the existing `ReleaseVerifier` port without installing it in CLI/CI.
`release_readiness.py` adds a separately authorized read-only API over typed,
reviewed installation policy, pinned gh/root files and explicit read-only token.
It composes the existing collector/verifier on a fresh bounded transport and
returns only anonymous status/fixed blockers, always `productionAuthorized=false`.
It accepts no JSON-to-installation/facts or dynamic module/path configuration.
`release_transport.py` confines authenticated GitHub reads to the two repositories
and public GHCR immutable-byte reads to ProjectRouter; a fixed blob CDN receives
no credentials. `release_source.py` establishes main ancestry, applicable active
protection, App-bound mandatory checks and same-SHA workflow/publication identity.
Publication links the exact `ci.yml` push run to its pinned referenced reusable
`publish.yml`, attempt-specific successful publication job and App-bound check run
in the same check suite. Signed build-origin must bind that caller run/attempt.
Unreadable rules, bypass details or incomplete pages block. Manager manifest/policy
hashes come from exact-SHA repository bytes, never echoed candidate claims.

`release_crypto.py` wraps the existing acceptance gh/Sigstore command and statement
selector with separately pinned installed binary/root, local bundles/subject bytes,
private bounded output and no inherited credentials. Source/signer certificate
constraints, SLSA builder and signed origin/publish-run predicates precede
`release_collector.py` constructing `ReleaseFacts`. There is no second OCI mapper,
cryptographic signature implementation, facts deserializer or production authority.
Trusted Python ports retain ADR 0001's installed-code boundary.

`caddy-release-qa.yml` runs `make qa-release-check` on PRs into develop/main
with read-only contents/attestation access and no App credentials. It pins the
historical Caddy digest `sha256:cd65a6b7daa8029c9f367dcacb9dab14a5eb016c7e584e3ca7f1dbed89ed3800`,
source `3d24ca2cefb723d8c8796940691bbbe31b19c1b2`, publication `40024294624/1`
and attestations `59250326` / `59250361`. The shared `verify_caddy_artifact`
contract checks OCI bytes/config and both pinned-root gh signatures, exact source
and certificate workflow/ref identity, workflow-based SLSA builder, signed source
and invocation, and the complete published input predicate. PR code supplies only
QA evidence (`productionAuthorized=false`, protected source unverified); the
production collector still calls full `ProtectedSource` policy B first. Operator
approvals and production release verification remain independent mandatory gates.
The transport accepts only the exact GitHub API-version deprecation Link notice;
pagination, mixed or unknown Link headers still fail closed.

Full `make check` runs once in Offline CI. Trusted Caddy image runs targeted
producer/auth regressions, authenticates upstream inputs, checks toolchain drift,
builds without network and validates the built image. Its unchanged publisher
guard waits for successful exact-SHA main push Offline CI and full policy B
before any registry write. Other isolated Docker/Caddy workflows stay separate.
Real release QA is networked and never a dependency of offline `make check`.
A manager SHA outside protected main remains blocked; develop/PR success cannot
replace a separate reviewed main merge and exact-SHA checks.

Controlled offline integration is verified; no real GitHub/GHCR collection was
authorized/run here. The official Caddy selected bytes and plain hash-linked SLSA
v0.2 remain insufficient publisher authentication; Caddy admission is blocked,
with alternative security policies left for separate review. Production execution,
authenticated actual state, operator issuance, DNS/TLS/storage/recovery and explicit
first-operation approval remain separate prerequisites. Image digests and admission
policy are unchanged.

## Stages 09.4B.5–09.4B.6 trust readiness boundary

[Trust readiness](docs/production-trust-readiness.md) prepares explicit low-level
`ProtectedSource` policy B with zero required PR approvals; every other source/CI
constraint remains. The sole maintainer approved B; the production collector (both
repositories) and independent bundle reviewer explicitly share the fixed trusted-code
`PRODUCTION_REVIEW_POLICY` constant. Low-level ProtectedSource still defaults to A;
no untrusted input selects policy. Independent human review is not guaranteed.
Readiness inherits B and always returns `productionAuthorized=false`. Read-only
GitHub/official Caddy registry observations are diagnostic evidence only. The selected Caddy digest
still lacks established cryptographic publisher/builder provenance; acquisition and
bootstrap remain blocked. No administrative/approval/source attestor is installed,
no new engine or crypto is added and no VDS operation is authorized or performed.
