# C4.7 — First installation: administrative evidence and sealed phase handoff

> Historical preparation/installation milestone. Full server deployment and the
> first ProjectRouter rollout have since completed per operator (2026-10-28).
> See [current status and delivery evidence](production-status.md). Statements
> below about missing prerequisites or unperformed operations describe the
> original review; do not rerun initial installation on the deployed host.


**Prepared offline; installation BLOCKED. No SSH, key creation, approval, release,
installation, start, image pull, bootstrap or rollout was performed.** All commands
below are future operator actions, subject to the separate permissions at each stop.
The C4.5 reports are historical evidence, not approved external inventory. This
document supersedes C4.6's missing-authority/transition design, not its host facts.

## Minimal supplementary inventory review

Do not rerun C4.1. Only the following gaps remain in C4.5/C4.6 inventory evidence.
Collect them over the existing `portfolio_admin` SSH channel with the independently
verified host key, only after separate **read-only contact** permission. Preserve
bounded output privately with UTC time, host key and administrator identity.

1. Resolved Python executable metadata, plus missing ancestors of the seven
   already identified tool paths. These are the exact supplementary commands;
   timeout/error/denial is UNKNOWN, never absence:

   ```sh
   timeout 3s /usr/bin/readlink -e /usr/bin/python3
   timeout 3s /usr/bin/stat -c '%n %F %u %g %a %h %d %i' \
     / /usr /usr/bin /usr/bin/python3.12 /usr/libexec \
     /usr/libexec/docker /usr/libexec/docker/cli-plugins
   timeout 3s /usr/bin/sha256sum /usr/bin/python3.12
   ```

   The expected regular Compose path is already known:
   `/usr/libexec/docker/cli-plugins/docker-compose`. Do not rediscover all four
   paths or rehash all seven tools just to fill the missing Python record. If
   Python resolves differently, stop; do not silently substitute a new pin.
2. Complete external dependency provenance/closure. This is an independent
   artifact-review job, not another broad host scan. Use authenticated Ubuntu/
   Docker package indexes and exact signed package archives on the workstation
   to enumerate the resolved Python executable, stdlib/extensions and native
   libraries, Docker/Compose/ip/ss/OpenSSH dependencies and CA files. Account for
   configured interpreter and dynamic-library search paths. Review the separately
   published gh 2.102.0 binary/roots against independent publisher evidence.
   After fixing this exact private file list, obtain only its missing hashes and
   protected ancestor metadata on the host. `sha256sum --check` against that list
   compares bytes; it cannot authenticate a list derived from the same host.
   Do not execute `ldd` or an unreviewed binary to establish its own provenance.
   Reject symlink spellings, non-regular/hardlinked files and missing closure;
   record resolved regular paths. Include helper/admin binding, operator public
   trust file and every file actually used by fixed authority/release code.
3. Backing mount/quota evidence **only if** an enforced-quota option is selected.
   For the recommended operator-supervised budgets, no speculative quota scan
   is needed. After storage exists, existing `LocalHostSources` supplies actual
   free bytes/inodes/device metadata; counters do not prove a reservation.

Fresh machine/daemon identity, complete conflict facts, destination absence and
external hash checks in the actual install/operation window remain mandatory
existing gates. They are not a reason to repeat the historical full preflight now.
No new host evidence is supplied by this PR.

## Who attests what

The owner approves allocation, budgets, recovery arrangements, key custody,
administrative channel and each operation. Technical reviewers establish exact
source/CI/OCI/signatures, runtime acceptance, package closure, bytes/metadata,
actual resource IDs and live postconditions. Capacity counters do not approve a
budget; a chosen rebuild strategy does not prove restore or containment.

The concrete proposed authority uses the **existing verified portfolio_admin SSH
login and existing sudo**, no new server, key, sudoers entry or command service.
The root administrator independently reviews and explicitly publishes two fixed
data documents. This publication is a privileged approval act, never a consequence
of a test, report parser, matching hash or successful CI. Root compromise already
defeats both this boundary and SealedInstallation; this does not claim otherwise.

* `/var/lib/portfolio-deployment-bootstrap/installation-decision.json`: canonical
  ASCII JSON of exactly `InitialInstallationDecision`'s C4.6 fields. Helper and
  admin-binding hashes occur in the sorted external inventory; its hash is SHA256
  of canonical sorted `[path, hex_sha256]` pairs. The document is **not** part of
  that inventory: it is administrative authority data. This avoids embedding the
  admin binding's own hash in its source. Max 2 MiB, root-owned 0600, protected
  ancestry, private 0700 intake directory, <=1 hour lifetime (recommend 15 minutes).
  Independently installed/reviewed `admin_bindings.py` now selects
  `authenticated_file_decision(bundle_sha256)` through its fixed entry point.
  Missing helper or separately published decision still blocks.
  No argument/env/request can select this implementation.
* `/etc/portfolio-deployment/phase-review.json`: bounded canonical JSON, root:root
  0600 with protected ancestry; <=1 hour lifetime, recommend 15 minutes. This is
  mutable **administrative data**, explicitly outside the sealed executable/tool
  inventory, like the protected operation spool. Its reader and the complete
  finite phase wiring are sealed code. Only separately authorized administrative
  publication/replacement may change it. Never add it as a changing file pin to
  `installation.json` or edit sealed code to change a phase.

`authenticated_file_decision` rechecks actual machine, intake hash and external
files, then exclusively writes/fsyncs `spent-<decision_id>` **before** calling the
installer. Concurrent use/replay fails. Retain the marker even if install fails
or the terminal is lost; do not unlink/retry. Diagnose first; a new administrative
decision needs independent re-review. The installer still rechecks everything
and never starts/enables a unit. A decision alone does not install anything.

`AdministrativePhaseReview` supplies the existing prerequisite and Caddy runtime
acceptance ports. Exact fields (no `approved` boolean):

| Field | Required value |
| --- | --- |
| `version`, `phase`, `manager_sha` | `1`; `empty-host` or `rollout`; exact approved protected-main SHA |
| `host_policy` | All fields of `HostPolicy`: actual host/daemon, three phase hashes, selected minimum bytes/inodes and five independently reviewed evidence references |
| `not_before`, `expires` | Integer epoch seconds; current bounded administrative window |
| `administrator`, `decision_id` | Actual reviewing administrator label and unique 32 lowercase hex decision ID; labels alone are not authentication |
| `caddy_runtime_hash` | `hashed(runtime.render().model)` of the independently reviewed exact runtime |
| `caddy_evidence_hash` | `hashed(asdict(evidence))` from the real CaddyReleaseCollector result |
| `runtime_acceptance` | Private archived review reference covering report SHA256, exact digest/source/run/attempt/workflow and successful real acceptance, reviewer and revocation disposition |
| `rollout` | `null` for empty host; otherwise exact `container_id`, `proxy_id`, `private_id`, and complete reviewed `infrastructure` list of `[kind, id, name, facts_hash]` from existing `infrastructure_key(resource)` |

Use `scripts.reconciliation.encode(document).encode()` for these canonical bytes;
the initial admin decision uses `operator_bootstrap.canonical(document)`. No sample
approval file or issuer is shipped. The live prerequisite reader still compares
all actual facts and thresholds; Caddy provenance/OCI/signatures run independently
before exact runtime evidence comparison. The administrative reviewer must verify
the acceptance report's origin and subject, not just copy a reference string.
At every verification, the phase document is reread and checked for equality and
expiry. Remove it to revoke; replacing it invalidates existing review objects.
These documents grant **no SSHSIG operation authorization**.

## Fixed bindings and the phase transition

Keep `installation/bindings.py` owner policy unset until the inputs below are approved.
The [concrete fixed recipe and public adapter](production-final-bindings.md) are now
implemented; their independently selected policy/observer inputs remain required.
Prepare one independently reviewed replacement containing **both** fixed recipes
before bundle build. Its `build(installation)` calls
`phase_bindings.build_reviewed_phase(installation, empty_host=..., rollout=...)`.
The two mappings are literal trusted Python composition over existing ports, not
JSON, env modules or callbacks supplied by requests. The function docstring lists
the exact keys supplied by the recipe versus the administrative document.

Use existing `EngineContract` with actual machine/daemon and
`unix:///run/docker.sock`; `BootstrapNetworkContract` with no existing ID;
`RuntimePaths` with the existing fixed `/srv/portfolio/{state,locks,journal,
caddy-config,certificates,caddy-data,caddy-admin}` paths; real GitHubRead,
repository-specific GHCRRead/AttestationVerifier and independently reviewed
WorkflowChecks. Bind the ProjectRouter image registry using
`composition.project_router_image_registry`. A protected read credential must
actually read both repositories' enforcement evidence. Never use a test authority.
The source/crypto/public-route ports must be real fixed implementations, not
always-success functions. No ready-to-run private binding is asserted by this PR.

1. Install the sealed bundle/storage/unit without a phase document or start. The
   installer creates real directory/lock inodes but no SQLite/config/cert/LKG.
2. Under the canonical lease, collect existing local host/Engine/storage facts.
   Independently review them and publish the exact `empty-host` HostPolicy in
   the administrative document. This solves the storage-inode ordering without
   predicting inodes or rewriting the bundle. Approve exact cached Caddy bytes
   and runtime acceptance; repeat policy review if cache inventory changed.
3. Collect/sign/submit the separate bootstrap candidate. Existing executor checks
   it again under lock, consumes its SSHSIG nonce durably, creates resources and
   performs two stable actual postcondition rounds before bootstrap LKG promotion.
   Private-network gateway `.1` is an expected postcondition; current creator
   lets Docker allocate it, and rejects another result. It is not a preexisting ID.
4. Stop. Independently observe actual IDs, labels/IPAM/membership, ACTIVE empty
   config, image/config, UDS inode/peer PID/storage and successful bootstrap ledger.
   Review `infrastructure_key` for **all** infrastructure, including default Engine
   networks. Publish a separately approved `rollout` document with new phase
   hashes and observed IDs. Do not accept C4.6's in-process IDs as owner approval.
5. The same sealed phase recipe checks the real lease-aware journal's successful
   bootstrap on the same host/daemon/Manager SHA. It wires actual IDs into existing
   `composition.build`, PinnedCaddyRuntime/ACTIVE/SourceTrust and private readiness.
   Those ports reobserve identities/security; neither a document nor journal LKG
   substitutes for their proofs. No resealing, automatic upgrade or installed-code
   edit occurs. Missing/changed IDs, unfinished state or revoked review blocks.
6. Collect/sign a distinct ProjectRouter candidate. Image preparation, if needed,
   uses its own scope/approval and changes inventory: obtain a new reviewed policy
   before collecting rollout. A bootstrap signature never authorizes rollout.

Full rollout also needs an independently reviewed **real public postcondition port**
for the admitted domain/TLS/path, as required by existing composition. The repository now supplies the concrete adapter and signed external-witness
consumer described in [final bindings](production-final-bindings.md). Independently
approved address/CA/content/observer policy and fresh live off-VDS evidence remain
mandatory. An internal health result or unsigned protected report cannot replace them.

## Commands and authorization stops

Run workstation commands from independently reviewed tooling, not candidate bundle
code. Keep private documents/artifacts under `/secure` (a workstation convention,
not a new VDS installation path). Obtain every `APPROVED_*` value independently;
an unset field stops the procedure. Do not reuse the historical C4.4 ZIP/SHA.

**Artifact preparation/review, unprivileged workstation.** After separately approved
protected-main release selection and target Ubuntu Python 3.12 vendor review:

```sh
.venv/bin/python -m scripts.production_integration.install_bundle \
  --revision "${APPROVED_MANAGER_SHA:?}" --vendor /secure/vendor \
  --caddy /secure/caddy --dependency-pins /secure/runtime-files.json \
  --bindings /secure/bindings.py --output /secure/bundle-a.zip
.venv/bin/python -m scripts.production_integration.install_bundle \
  --revision "$APPROVED_MANAGER_SHA" --vendor /secure/vendor \
  --caddy /secure/caddy --dependency-pins /secure/runtime-files.json \
  --bindings /secure/bindings.py --output /secure/bundle-b.zip
cmp /secure/bundle-a.zip /secure/bundle-b.zip
sha256sum /secure/bundle-a.zip
```

Both output paths must be absent. Independent reviewer, with independently sourced
asset pins in `/secure/reviewed-assets.json`, independently reviewed binding hash
and read-only GitHub credential, performs the actual existing verifier:

```sh
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
from scripts.production_integration.bundle_review import ManagerBundleVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GitHubRead, HTTPSRead
v = ManagerBundleVerifier(
    github=GitHubRead(os.environ['GH_READ_TOKEN'], HTTPSRead()),
    checks=(WorkflowCheck('DanilFaritovich/portfolio-deployment', 'Offline checks',
                          15368, '.github/workflows/offline.yml'),),
    asset_pins=tuple(sorted(json.loads(Path('/secure/reviewed-assets.json').read_bytes()).items())),
    bindings_pin=os.environ['APPROVED_BINDINGS_SHA256'])
result = v.verify(Path('/secure/bundle-a.zip').read_bytes())
assert result.manager_sha == os.environ['APPROVED_MANAGER_SHA']
assert result.bundle_sha256 == os.environ['APPROVED_BUNDLE_SHA256']
print('independent bundle/source/assets verified; no installation authorized')
PY
```

Do target-ABI isolated imports and verify the full **external** file list separately;
ManagerBundleVerifier does not verify VDS libraries or grant administrative authority.
Archive source/wheels/provenance, both ZIPs, receipts and inventories off VDS.

**STOP — exact administrative installation permission.** Only after approval of
machine/Manager/ZIP/helper/binding/external inventory tuple and decision window,
transfer reviewed bytes using existing portfolio_admin SSH with
`StrictHostKeyChecking=yes`, `UserKnownHostsFile=<independently verified file>`,
port 2222. Never use accept-new, root SSH, disabled host checking or a root checkout.
Authenticated admin intake must provision helper directory/files and fixed bundle/
decision paths exclusively, root:root 0700/0600; refuse existing conflicts.
Review/pin helper/admin code **before first execution**. Once those already-reviewed
files are in their fixed paths, invoke only the fixed helper. For example, after
explicit authorization, prepare a private staging directory on the existing admin
account; do not reuse a preexisting temporary directory:

```sh
umask 077
mkdir /tmp/portfolio-install-review
```

From the workstation, transfer only the reviewed files (set approved host/key file
explicitly; these variables are not an alternative to verifying that host key):

```sh
scp -P 2222 -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${APPROVED_KNOWN_HOSTS:?}" \
  /secure/operator_bootstrap.py /secure/admin_bindings.py \
  /secure/bundle-a.zip /secure/installation-decision.json \
  "portfolio_admin@${APPROVED_HOST:?}:/tmp/portfolio-install-review/"
```

In the authenticated administrative session, during a quiescent window, stop on
any failed command; both destination directories must be genuinely absent:

```sh
set -eu
sudo -n /usr/bin/test ! -e /usr/local/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/test ! -L /usr/local/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/test ! -e /var/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/test ! -L /var/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/mkdir -m 700 /usr/local/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/mkdir -m 700 /var/lib/portfolio-deployment-bootstrap
sudo -n /usr/bin/install -o root -g root -m 600 \
  /tmp/portfolio-install-review/operator_bootstrap.py \
  /tmp/portfolio-install-review/admin_bindings.py \
  /usr/local/lib/portfolio-deployment-bootstrap/
sudo -n /usr/bin/install -o root -g root -m 600 \
  /tmp/portfolio-install-review/bundle-a.zip \
  /var/lib/portfolio-deployment-bootstrap/bundle.zip
sudo -n /usr/bin/install -o root -g root -m 600 \
  /tmp/portfolio-install-review/installation-decision.json \
  /var/lib/portfolio-deployment-bootstrap/installation-decision.json
sudo -n /usr/bin/sha256sum \
  /usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py \
  /usr/local/lib/portfolio-deployment-bootstrap/admin_bindings.py \
  /var/lib/portfolio-deployment-bootstrap/bundle.zip \
  /var/lib/portfolio-deployment-bootstrap/installation-decision.json
```

**Compare these hashes to independent workstation approval, then stop for the
explicit install action.** Never execute merely because the copy succeeded.
The administrative decision publication above is itself privileged approval;
it must not be folded into automated report processing. Partial intake directories
are diagnostic state, not permission to overwrite them. The exact install command:

```sh
sudo -n /usr/bin/python3 -I -S -B \
  /usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py
```

Expected: `installed`, `executorStarted=false`. Do not proceed on lost output,
partial install or a consumed marker; independently diagnose. No systemctl command
is part of installation. Installation issues no approval, phase document or LKG;
its inventory and the spent administrative marker retain only the installation facts.

**STOP — separate image acquisition and bootstrap permission.** Independently use
CaddyReleaseCollector to verify the selected exact producer/source/OCI/signatures;
review real exact-digest runtime acceptance before approving cache acquisition.
Only if separately authorized, existing admin channel may run:

```sh
sudo -n /usr/bin/docker --host unix:///run/docker.sock pull "${APPROVED_CADDY_IMAGE:?}"
```

Inspect actual cached image with existing `bind_caddy_engine`; do not infer it from
the pull's exit status. Recollect phase hashes, review/publish empty-host document.
To collect the bootstrap candidate, an authenticated administrator can execute the
following reviewed isolated snippet on the installed host. It is read-only, accepts
no requests/modules and contacts only the fixed installed readers. Do not put the
canonical lease around `bindings.build`: rollout binding acquires its own lease.

```sh
sudo -n /usr/bin/python3 -I -S -B - <<'PY'
import os, stat, sys
from pathlib import Path
for p in ('/', '/opt', '/opt/portfolio-deployment', '/opt/portfolio-deployment/vendor'):
    s = Path(p).lstat()
    assert os.geteuid() == 0 and s.st_uid == 0 and stat.S_ISDIR(s.st_mode) and not s.st_mode & 0o022
sys.path[:0] = ['/opt/portfolio-deployment', '/opt/portfolio-deployment/vendor']
from dataclasses import asdict
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.installation import SealedInstallation, protected
from scripts.production_integration.locking import production_host_lock
from scripts.reconciliation import encode
protected('/opt/portfolio-deployment/installation.json')
d = parse_snapshot(Path('/opt/portfolio-deployment/installation.json').read_bytes())
installation = SealedInstallation(d['manager_sha'], tuple(tuple(p) for p in d['files']))
installation.verify()
import bindings
executor = bindings.build(installation)
with production_host_lock() as lease:
    prepared = executor.preflight.prepare_locked('first-bootstrap', lease)
print(encode(asdict(prepared.candidate)))
PY
```

Privately transfer/review the **entire** candidate on the signing workstation.
Use the exact payload/envelope procedure in [operator-approval.md](operator-approval.md#offline-signing-and-inspection),
then sign (only after separate owner operation approval):

```sh
ssh-keygen -Y sign -O hashalg=sha512 -f /secure/operator-signing-key \
  -n portfolio-deployment-operation-v1 /secure/payload.json
```

Prepare the existing closed request locally, using that procedure's envelope:

```sh
.venv/bin/python - <<'PY'
import base64
from pathlib import Path
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.operator_approval import canonical
candidate = parse_snapshot(Path('/secure/candidate.json').read_bytes())
request = dict(candidate=candidate, envelope=base64.b64encode(
    Path('/secure/envelope.json').read_bytes()).decode('ascii'))
Path('/secure/operation.json').write_bytes(canonical(request))
PY
```

Authenticated root intake must atomically publish only those reviewed request bytes
as `/srv/portfolio/requests/operation.json` (root:root 0600). Requests contain no code,
paths or ports. One explicit administrative publication (after authenticated
transfer to private staging and independent byte comparison) is:

```sh
sudo -n /usr/bin/test ! -e /srv/portfolio/requests/.operation.pending
sudo -n /usr/bin/test ! -L /srv/portfolio/requests/.operation.pending
sudo -n /usr/bin/install -o root -g root -m 600 \
  /tmp/portfolio-install-review/operation.json /srv/portfolio/requests/.operation.pending
sudo -n /usr/bin/sha256sum /srv/portfolio/requests/.operation.pending
```

After comparing that digest to the separately signed/reviewed request, atomically
publish with `sudo -n /usr/bin/mv -T /srv/portfolio/requests/.operation.pending
/srv/portfolio/requests/operation.json`. Archive previous request/signature on the
workstation before an explicitly approved replacement; never reuse a spent request.
Use the same private staging/check/rename pattern for `phase-review.json` under
`/etc/portfolio-deployment` after each explicit administrative review; that protected
directory must already be provisioned with operator trust before external inventory
approval. Publication tools belong to the independently reviewed admin channel,
not an executor command service. Nothing executes these commands automatically.

**STOP — separate exact unit recognition/start permission.** Check installed unit
bytes against its approved pin before administrative recognition and one-time start:

```sh
sudo -n /usr/bin/systemctl daemon-reload
sudo -n /usr/bin/systemctl start portfolio-deployment-executor.service
sudo -n /usr/bin/systemctl show portfolio-deployment-executor.service \
  --property=ActiveState,SubState,Result,ExecMainStatus
```

Do not enable/restart/retry. Verify executor result, both postcondition rounds and
successful journal/LKG independently; unit exit alone is insufficient.

**STOP — transition review, then separate ProjectRouter approval.** Follow phase
steps 4–6 above. Repeat candidate collection with scope
`prepare-project-router-image` if cache preparation is needed; separately sign,
submit and start that scope. Then review updated phase hashes. Collect
`first-project-router-rollout`, separately sign/submit/start using the same commands.
The fixed rollout binding must include real public postconditions before this step.
Verify internal readiness, ACTIVE routes and public HTTPS `/en/` with certificate
validation on an independent workstation:

```sh
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
  --proto '=https' --output /dev/null https://danil-miftakhov.dev/en/
```

Compare DNS destination/certificate and observed route to the approved host; this
manual smoke alone does not implement the executor's public postcondition port.
No `curl -k`, manual Caddyfile edit or
raw Compose rollout is an alternative. Stop on any ambiguity; preserve all data,
lock inode, receipts and journals. No prune/down/reset/recovery replay is authorized.

## Only outstanding owner decisions

Fill these exact fields privately. Technical hashes/IDs/reports are reviewer
deliverables, not choices the owner must invent. No value in this table is approved.

| Decision fields | Recommendation |
| --- | --- |
| `proxy_cidr`, `proxy_gateway`, `private_cidr`, `private_gateway`, `fixed_paths` | Approve `172.30.240.0/24`, `172.30.240.1`, `172.30.241.0/24`, `172.30.241.1`, existing paths after allocation review |
| `minimum_bytes`, `minimum_inodes`, `certificate_budget_bytes`, `budget_owner`, `budget_mechanism` | 1073741824, 100000, 268435456; name operator for budget supervision (planning envelopes in C4.6). Enforced quotas require real separate implementation/evidence |
| `rebuild_operator`, `replacement_access_budget`, `backup_destination`, `backup_owner`, `backup_retention`, `key_custodian`, `restore_reviewer`, `old_host_isolation`, `console_equivalent` | Rebuild on new VPS; encrypted off-host backup; specify destination/retention/custodians, provider-side isolation independent of SSH and console or explicit tested equivalent. Actual restore/containment evidence is still technical work |
| `administrative_channel`, `administrative_reviewer`, `window_seconds`, `revocation_owner` | Existing portfolio_admin + verified host key/sudo, reviewed fixed-file mechanism above; 900 seconds, removal/replacement by named administrator |
| `operator_principal`, `operator_public_key`, `signing_custodian`, `passphrase_policy`, `encrypted_key_backup`, `rotation_revocation_owner` | Separate off-VDS Ed25519 identity, passphrase, encrypted backup; approve actual public key/fingerprint after separately authorized creation or selection |
| `release_permission`, `selected_manager_sha`, later exact install/start/cache/bootstrap/rollout permissions | Review/merge/release separately; then select actual protected-main SHA with required checks. Defer installation decision tuple until artifacts/inventory are independently reviewed. Authorize each live step separately |

Right now installation is prevented by **unapproved choices and missing private
reviewed inputs**, not a demonstrated Docker/gh/Compose failure: resolved Python/
external provenance closure, recovery evidence, operator trust, protected release,
complete fixed private wiring and independently verified bundle/admin decision.
This PR supplies authority consumers/finite transition, not those approvals. Full
rollout additionally lacks a real fixed public postcondition implementation and
authorized live acceptance. Next owner action: fill the first five rows and grant
only the narrow supplementary read-only collection if desired; do not authorize
installation on a report hash alone.
