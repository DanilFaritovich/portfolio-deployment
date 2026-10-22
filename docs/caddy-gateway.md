# Stage 04 — Offline Caddy Gateway Foundation

Implemented offline: immutable routes/candidates, deterministic Caddyfile rendering,
Stage 03 plan adaptation, conservative full previews and opt-in local official
validation. There is no running gateway, live discovery, deployment or reload.
Python reuses the existing offline tooling; production controller runtime remains
undecided. Application/admission v1 and the Stage 03 contract are unchanged.

## Ownership and topology

One application manifest describes one future independent Compose project; one
component maps to one service. There is at most one public ingress per application.
Caddy is the only intentional public HTTP(S) entry point. Application components
publish no host ports; only the HTTP ingress joins the future shared proxy network.
Backend, databases, workers and private dependencies stay private unless an HTTP
component is explicitly selected as ingress. Applications own internal HTTP
routing; central Caddy only maps hostname to ingress alias and container port.

Caddy serves HTTPS; it neither publishes Docker images nor manages Docker Engine.
Application repositories still own source, CI and image publication.

## Models and APIs

`scripts/gateway.py` contains the pure APIs:

```python
from scripts.gateway import desired_routes, render_caddyfile, build_gateway_candidate
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import build_reconciliation_plan

desired = build_desired_state(".", "a" * 40)  # synthetic revision, replace explicitly
observed = load_observed_state("tests/fixtures/observed/noop.json")
plan = build_reconciliation_plan(desired, observed)
candidate = build_gateway_candidate(desired, observed, plan)
```

`GatewayRoute` is frozen: application, ingress component, hostname, internal port,
derived alias, manager ownership and optional desired Git revision. Alias is always
`<application-id>-<component-name>`; manifests cannot override it. Desired routes
carry their supplied revision; observed routes have no invented applied revision.
The `key` excludes revision, so changing Git SHA or image/config reference identifiers
alone cannot change routing. Hostnames, IDs, alias lengths, integer ports, exact
manager ownership and unique application/hostname/alias identities are checked.
Desired ingress must match an existing HTTP component/port and derived proxy
membership. The Stage 02 input API performs schema, admission and global alias
checks; the gateway does not implement a competing admission policy.

`GatewayCandidate` is a frozen canonical JSON value, following Stage 03's model
pattern. `.document` returns a detached copy; `.to_json()` emits deterministic JSON
plus one newline. Model version is `portfolio.gateway-candidate/v1`. It records
revision, desired/current/candidate configurations, route outcomes, reasons,
required future checks and `applied: false`, `authorization: false`.
`build_gateway_candidate(desired, observed, plan)` requires the matching Stage 03
plan; a stale/unrelated plan is rejected. Recomputing the pure comparison verifies
that association without modifying Stage 03 or inventing another deployment plan.

## Desired, active and selected routes

- `desired` projects admitted enabled declarations. Disabled declarations reserve
  identities but request no new public activation. Internal-only apps have no route.
- `current` projects only routes explicitly observed `present`, using their observed
  ingress configuration and exact ownership. `enabled: true` is never evidence of
  activation. An old hostname need not remain admitted in the new desired policy
  to be retained for a lifecycle decision.
- `candidate` is the complete **retained** route selection, equal to current in
  Stage 04. New, changed and removed desired routes remain review proposals until
  future deployment/readiness/permission evidence allows selection. Rendering the
  desired preview is not an activation instruction.

`gatewayChangeRequired` compares routing parameters of desired versus current;
`candidateDiffersFromCurrent` separately reports whether selection changed (false
in this foundation). Current configuration is reconstructed from comparable v1
route parameters, not a discovered byte-for-byte running Caddyfile. Arbitrary
manual Caddy directives are outside the snapshot contract and cannot be assumed
safe to replace. Without actual gateway evidence, preview is never safe to apply.

| Candidate status | Meaning |
| --- | --- |
| `no-op` | Comparable routes match; runtime image/config differences cause no gateway reload |
| `changes` | Route-only difference proposed for review; selected routes still retained, switch checks outstanding |
| `pending` | New/network-aware activation or route removal needs future gates/lifecycle approval |
| `blocked` | Stage 03 blocked/degraded, unknown/incomplete/untrusted evidence, ownership or active-host conflict |

Precedence is blocked, pending, changes, no-op. Per-application blockers propagate
from Stage 03, including component/storage/OCI/health blockers; a blocked app never
receives a changes outcome. Lifecycle entries remain blocked according to Stage 03
and carry `pending-lifecycle-approval`. Ingress removal without application removal
carries `pending-removal-decision`. Neither disables nor removals revoke active
routes automatically. Retention is not approval for continued operation.

New applications require components-created, proxy-network-membership,
upstream-reachability, target-readiness, deployment-permissions and switch approval.
Route-only changes require target/switch checks without inventing container changes.
Ingress component changes reuse Stage 03 `route-change` and `proxyNetwork`
comparison, retaining explicit future network gates. No check is marked completed.

Every observed active route is retained, including unrelated and orphan routes.
A proposed hostname already held by another active application blocks takeover.
Unknown/foreign/ambiguous routes, duplicate observed hostnames/aliases or incomplete
scope, or unknown/inconsistent observed HTTP ingress/port/proxy membership make
a safe full set unavailable: both `current` and `candidate` are null,
never partial Caddyfiles. Desired remains a separate preview. Known full route sets
can still be retained when unrelated Stage 03 runtime blockers prevent activation.

`trusted` and `complete` are provider assertions about the entire registry routing
universe, not cryptographic verification or proof of a live Caddy. There is no
freshness, readiness, applied-revision or permission evidence in snapshot v1.

## Deterministic renderer and validation

`render_caddyfile(routes)` sorts by hostname and emits only this fixed template,
with four spaces, blank lines between hosts and one final newline:

```caddyfile
router.example.test {
    reverse_proxy project-router-web:8080
}
```

No user directives, TLS overrides, shell commands, wildcards, path routes, catch-all
or secrets are accepted. Renderer output depends only on routing parameters,
independent of input/YAML order and revision. Configuration SHA-256 covers the exact
UTF-8 Caddyfile bytes, not revision metadata. Empty sets return `None` and an explicit
`no-routes` outcome, with null Caddyfile/hash. Stage 04 deliberately does not claim
an empty Caddyfile passes Caddy or invent a fallback listener. Future removal of
all routes needs its own lifecycle-approved representation and official validation.

Internal invariants and offline tests do **not** constitute `caddy validate`.
`scripts/gateway_validation.py` accepts normalized routes, regenerates the fixed
Caddyfile, locates an operator-provisioned trusted official `caddy` on PATH and runs:

```sh
caddy version
caddy adapt --config candidate.Caddyfile --adapter caddyfile
caddy validate --config candidate.Caddyfile --adapter caddyfile
```

[Official Caddy CLI documentation](https://caddyserver.com/docs/command-line)
explains that adapt is weaker than validate, which provisions modules without
starting the configuration. The adapter uses `shell=False`, fixed arguments,
10/15-second timeouts, temporary configuration and isolated HOME/XDG storage.
Command stdout/stderr is discarded, version is reduced to a bounded version token,
and failures use fixed reason codes. It never reloads, calls Admin API, starts a
persistent process or downloads Caddy. Binary provenance is an operator prerequisite,
not proven by a filename/version. No external certificates or user TLS directives
are loaded. Only temporary local files are created and automatically removed.

Both adapt and validate must succeed for `passed`; adapt alone never suffices.
Missing binary reports `not-performed` / `caddy-not-installed`. A no-routes outcome
is not sent to Caddy. Official validation does not change candidate authorization,
active state or any plan status, and proves no DNS, HTTPS or upstream reachability.

## CLI and Makefile

```sh
make gateway-render PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa GATEWAY_FORMAT=json
make gateway-check PYTHON=.venv/bin/python
make gateway-validate PYTHON=.venv/bin/python REGISTRY_ROOT=. OBSERVED=tests/fixtures/observed/noop.json GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

The SHA is synthetic. Supply the intended full lowercase Git revision; no Git
lookup or production approval verification occurs. Registry root defaults to `.`
for Make. `GATEWAY_FORMAT=caddyfile` emits the retained full candidate only.
Prefer JSON when reviewing desired differences or an empty/blocked universe.
`gateway-check` runs the offline Stage 04 tests without Caddy. `make check` includes
all Stage 02–04 tests. `gateway-validate` explicitly invokes the optional binary
adapter and returns its report in JSON. It checks the retained candidate, never
silently substitutes desired configuration. The pure renderer can separately render
desired routes for review and the validation API can check such normalized routes;
neither operation selects them for activation.

Direct CLI: `.venv/bin/python -m scripts.gateway_cli --root . --observed PATH
--git-revision FULL_SHA --format json|caddyfile [--official]` (one shell command).

| CLI exit | Meaning |
| --- | --- |
| 0 | Successful JSON/full Caddyfile preview, including pending/changes; no activation authorization |
| 1 | Invalid arguments, dependencies, registry, snapshot or gateway input; no candidate stdout |
| 2 | Blocked candidate; JSON may contain retained current configuration, never partial routing |
| 3 | No renderable Caddyfile (`no-routes`); inspect JSON |
| 4 | Explicit official validation failed or was not performed; inspect JSON |

Blocked status takes precedence over official failure. JSON stdout contains only
JSON, Caddyfile stdout only configuration; input/format diagnostics go to stderr.
GNU Make maps nonzero recipe exits to its own failure code; direct CLI consumers
can distinguish the documented codes. Existing fix/validate/plan/check remain.

## Future safe reload interface and production prerequisites

Stage 04 exposes immutable candidates, hashes, comparison outcomes and validation
results as review inputs for a future executor; no apply/reload implementation or
readiness evidence schema is introduced. The future sequence is:

1. Obtain fresh observations and an admissible Stage 03 plan.
2. Establish upstream readiness and permission to publish.
3. Select and generate a complete Caddy candidate, retaining unrelated routes.
4. Validate that full candidate with the official compatible Caddy binary.
5. Preserve previous known-good configuration and evidence.
6. Serialize gateway changes.
7. Perform the supported reload.
8. Verify actually active routes with bounded checks.
9. Record outcomes and perform controlled recovery when needed.

This is a future procedure, not Stage 04 executable behavior. Stage 05 owns Docker
execution/network changes; Stage 06 owns readiness, recovery and durable results.
Stage 07 owns container E2E/reload tests, Stage 08 GitHub Actions automation, and
Stage 09 separately authorized production activation.

HTTPS issuance needs authorized real DNS and server/challenge reachability;
certificate issuance, public 80/443, DNS propagation and CA failures are production
prerequisites. Persist/protect Caddy certificate and config state across restarts.
Admin API must stay private. Caddy gets no Docker socket or Engine permissions.
The proxy network is not an absolute security boundary: members can reach peers.
Per-application networks versus a shared bridge, lateral access, trusted forwarded
headers/client-IP chains, firewall behavior and gateway runtime privileges remain
open production design decisions. Caddy alone guarantees neither application zero
downtime nor high availability. No Docker/Caddy/VPS production mutations occur in
this stage; offline success establishes none of those live prerequisites.

## Stage 09.3 integration

Stage 09.3 adds [managed runtime and gateway contracts](production-integration.md#networking-caddy-ownership-and-storage)
over these route/candidate models. The controller preserves unrelated routes,
requires readiness and host lock, skips identical routing, validates the full
candidate and preserves prior owned configuration before compare/reload. Fake
ports test validation/activation failure; live transports are not configured.
Pinned Caddy image/version, persistent certificate/config storage and exact proxy
IPAM/ownership require operator approval. No public admin API, socket, DNS/TLS
operation or production activation was added.
