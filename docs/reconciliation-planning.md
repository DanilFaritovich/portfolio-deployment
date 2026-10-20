# Stage 03 — Offline reconciliation planning

Stage 03 compares admitted Git declarations with an **explicit local fixture**.
It performs no Docker, Compose, Caddy, GHCR, DNS, Git, SSH or VPS discovery or
mutation. A snapshot is a claim supplied by the caller; neither it, the supplied
Git SHA nor a successful plan establishes live state or authorizes execution.
Application/admission v1 is unchanged. The real registry and policy remain empty.

## Models and API

```python
from pathlib import Path
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import build_reconciliation_plan

# Supply the intended full revision explicitly; no Git lookup occurs.
desired = build_desired_state(Path("."), "a" * 40)
observed = load_observed_state(Path("tests/fixtures/observed/noop.json"))
plan = build_reconciliation_plan(desired, observed)
print(plan.to_json(), end="")
```

`build_desired_state(root, git_revision)` first calls the complete Stage 02
`validate_registry(root)` admission API. It then constructs `DesiredState` with
revision and a sorted tuple of `DesiredApplication` values: identity, source
repository and normalized spec. The spec includes explicit enabled status,
component names/types/images/platform/ports/health/dependencies, application and
component config/secret reference IDs, storage/mounts and optional ingress.
`proxyNetwork` is derived for the ingress component only.

Models are frozen dataclasses containing only immutable strings and tuples of
immutable values. Structured payloads use canonical JSON **strings**, never frozen
dataclasses containing mutable dictionaries. `.spec` and `.document` accessors
return detached copies. Mutating the source registry, native snapshot or accessor
result cannot change a model. Construct input models through these adapters;
direct dataclass construction is an internal trusted-value interface.

`load_observed_state(path_or_dict)` validates a local JSON file or native JSON
object, normalizes it and returns `ObservedState`. `PlanningInputError` reports
invalid planning inputs; `RegistryValidationError` retains Stage 02 errors.
No partially validated state is returned. Missing installed schemas/dependencies
fail. Loading and validation are separate from the pure function
`build_reconciliation_plan(desired, observed) -> ReconciliationPlan`.

The comparison module imports no filesystem, network or execution adapters.
`ReconciliationPlan.document` returns a copy; `.to_json()` returns stable compact
JSON plus a newline; `.to_text()` returns a short deterministic human summary.
Python reuses existing offline tooling, without selecting the future controller
runtime, production source or trigger.

## Observed snapshot v1

The normative closed Draft 2020-12 [schema](../schemas/observed-v1.schema.json)
uses `apiVersion: portfolio.observed/v1`. A complete empty fixture is:

```json
{"apiVersion":"portfolio.observed/v1","scope":{"kind":"registry","complete":true,"trusted":true},"applications":[]}
```

`scope.kind: registry` means **all** application resources, components, storage
inventory and public routes in the supplied planning universe. `complete` claims
exhaustive observation; `trusted` claims evidence suitable for comparison. These
flags are explicit fixture assertions, not cryptographic verification or live
host attestation. Both must be true for actionable comparisons. Stage 03 supports
no partial-scope allowlist. An incomplete/untrusted snapshot blocks even an empty
registry; missing records in it never mean absence.

Each application record requires:

- `id`, `existence: absent|present|unknown`, `owner`, `components`.
- `storage`, `configRefs`, `secretRefs`: observed declarations, or `null` for unknown.
- `route`: existence, ownership and ingress-shaped `configuration` (null unless
  present). No route health probes or Caddy configuration are modeled.

Each component record requires:

- `name`, `existence`, `owner`.
- `runtime`: observed effective component fields from manifest v1, excluding
  `name` and `image`, plus required boolean `proxyNetwork`; null if unknown.
- `runtimeComplete`: whether this observation accounts for **all** effective
  runtime settings within the supported contract. Unsupported overrides must
  set this false; they cannot be silently omitted to obtain no-op.
- `imageIdentity`: null, or `pullReference`, `pullKind: manifest|index`,
  `manifestDigest`, `platform`, `relationship: verified|unknown`.
- `health: healthy|degraded|unknown`. This is an observation, not a readiness gate.

Ownership is `{manager, application}` for applications/routes and
`{manager, application, component}` for components. Manager is
`portfolio-deployment`, `foreign` or `ambiguous`. Only exact platform/application/
component ownership permits comparison. Names do not establish ownership.
A conflict blocks; it never allows adoption, overwrite or cleanup.

Full snapshot examples are [support-assistant](../tests/fixtures/observed/support-assistant.json),
[empty](../tests/fixtures/observed/noop.json) and
[incomplete](../tests/fixtures/observed/incomplete.json). Images, reference IDs and
hostnames are synthetic. The support fixture corresponds to the Stage 02 example
with `enabled: true`; it is not a production default.

With complete trusted scope, an omitted application/component is absent. Explicit
`unknown` always blocks, including in complete snapshots. Non-present components
cannot contain runtime/image evidence; non-present applications cannot contain
component records or a present route. Present records with null runtime or
application configuration block instead of implying defaults. Duplicate identities
are invalid, including duplicate absent records. Unknown fields are invalid;
unknown runtime evidence is represented explicitly, not as arbitrary raw data.

Inputs are bounded to 2 MiB, 128 applications, 32 components/application,
32 list elements for references/mounts/volumes, depth 32, 100000 value nodes,
512-character strings (with stricter schema limits) and 128-character keys.
Native API inputs receive the same bounds. JSON must be UTF-8, with unique object
keys and finite supported values; integral floats are not integer declarations.
JSON structural nesting is bounded before parsing. Diagnostics are fixed rules,
not raw parser messages, field values or unknown property names.

Files must be regular `.json` files. Parent traversal and symlinks in **any** path
segment are forbidden; dirfd/NOFOLLOW walking also rejects symlink replacement
while opening segments. Reads are capped even for growing files; FIFOs/devices
are rejected without blocking. This is bounded input handling, not an OS sandbox.

## Comparison, actions and precedence

The planner compares effective type, immutable pull reference, platform, HTTP
port, health declaration, dependencies, selected config/secret IDs, mounts,
storage and derived proxy membership. Ingress compares hostname, target and port.
Enabled false enters a separate lifecycle decision, never ordinary convergence.
Application-level reference inventory changes are recorded without restarting
components that do not consume the changed references.

Object keys and all **currently supported** v1 arrays have unordered/set or
identity semantics; they are normalized by canonical JSON value. Optional empty
reference/dependency/mount lists equal omitted lists. Component order does not
encode startup order; `dependsOn` remains explicit. There are no command/argument
arrays in this contract. A future order-sensitive field must preserve order and
extend normalization deliberately. Plan entries sort by application, component
(application entries first), then action. No timestamps, UUIDs or environment
ordering enter plan contents. Revision changes alone never recreate resources.

| Action | Meaning / safety boundary |
| --- | --- |
| `create` | Enabled application or component reliably absent; includes future preflight/readiness requirements |
| `update-component` | Only this component differs in supported effective configuration; unchanged siblings and routes remain no-op |
| `no-op` | Comparable effective parameters match, or disabled/removed resources reliably absent; external reference contents remain unobserved |
| `route-only` | Route differs and runtime network membership already matches; no component recreation |
| `route-change` | Route differs alongside required creation or proxy-membership change; includes component changes |
| `reference-inventory-change` | Application reference declarations differ; does not itself inject configuration |
| `health-observation` | Degraded component; independent from configuration diff, never an automatic restart |
| `disable-pending-approval` | Disabled application still exists; lifecycle decision required |
| `removal-pending-approval` | Owned application/component removed from desired declarations but still present; no automatic stop/delete |
| `blocked` | Unknown evidence, identity/ownership conflict, unsupported runtime, or storage/mount change requiring separate policy |

Missing components within an existing application carry `drift-missing-component`
separately from `create`. Other configuration discrepancies carry
`effective-configuration-diff` and `diff-origin-unproven`: without a trusted
last-applied baseline the planner cannot distinguish a declaration change from
out-of-band drift. It does not fabricate that history.

Every entry contains application/component identity, action, status, fixed reason
codes, warnings, relevant nonsecret before/after data and required future checks.
The plan contains format `portfolio.plan/v1`, explicit revision, per-application
summaries, final status, `executed: false` and `authorization: false`.
It is a comparison result, **not a sequential execution script**.

Final and application status precedence is `blocked > degraded > changes > no-op`.
Ownership/unknown blockers suppress unsafe action proposals. Any blocker in an
application marks its other proposed changes blocked as well, protecting related
dependencies and routes. Independent apps retain their own detailed outcomes;
a blocked overall plan must not be applied partially by assuming other entries
are authorization. Health degradation remains a separate entry even alongside an
image/config update. Health unknown blocks. Degraded known state may still show
configuration proposals, but exit 3 prevents treating it as an admissible plan.

## Safety limitations and future gates

An OCI index digest and its platform manifest digest identify different objects.
`pullReference` records the selected immutable reference; `manifestDigest` records
the running platform artifact. For `pullKind: manifest`, digests must match. For
`index`, the snapshot must explicitly assert a verified platform mapping; string
inequality alone neither proves an update nor proves equivalence. Unknown mapping,
identity or platform evidence blocks. No GHCR/index resolution occurs here; future
preflight must establish this evidence from actual artifacts and fresh runtime
state rather than trusting fixtures.

Matching config/secret IDs says nothing about external contents or versions.
Plans warn `reference-identifiers-only-content-and-versions-unobserved` when
references are declared. No secret contents are read, accepted or emitted.
Before future execution resolve versioned configuration references separately.
A no-op is limited to supported observable parameters, not a claim that external
secrets/configuration have not changed.

Existing storage declaration or mount changes block the application, even when
an additive change might eventually be safe. Separate ownership/lifecycle/migration
policy is required. Creating missing components never authorizes overwriting
existing data; future checks must establish persistent-resource ownership and
migration compatibility. No action deletes named volumes, remaps persistent
mounts, stops disabled/orphan resources or performs irreversible migrations.

Future Stage 04–06 work must implement gateway rendering/candidate validation,
artifact/platform preflight, fresh ownership discovery, locks, scoped execution,
configuration delivery, bounded readiness, migration policy, journals and recovery.
The listed `requiredChecks` are obligations, **not completed checks**. A stale
fixture cannot authorize a future deployment, regardless of exit code.

## CLI and Makefile

```sh
.venv/bin/python -m scripts.plan_registry --root . \
  --observed tests/fixtures/observed/noop.json \
  --git-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --format json
make plan PYTHON=.venv/bin/python \
  OBSERVED=tests/fixtures/observed/noop.json \
  GIT_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa PLAN_FORMAT=json
```

The repeated `a` SHA is an explicit synthetic example only. Supply the intended
full lowercase 40-character Git SHA; the CLI does not query Git or verify approval.
`--observed` and `--git-revision` are required; `--root` defaults to the installed
repository and `--format` defaults to `text`. Make requires explicit `OBSERVED`
and `GIT_REVISION`; `PLAN_FORMAT` defaults to text. No empty observation is invented.

| Exit | Meaning |
| --- | --- |
| 0 | Valid comparable plan, either changes or no-op; execution still unauthorized |
| 1 | Invalid/missing input, arguments, schema or dependencies; no plan output |
| 2 | Valid plan with blockers or pending lifecycle approval |
| 3 | Valid plan with degraded health and no higher-priority blocker |

Valid JSON plans go to stdout without prose prefixes; input errors go to stderr.
GNU Make itself returns its standard failure code for failed recipes; use the
Python CLI exit codes when a consumer needs to distinguish 1/2/3. Text mode gives
a short outcome/reason listing; JSON contains before/after, warnings and checks.
`make check` includes Stage 02 and Stage 03 offline tests; no GitHub Actions
workflow is installed before Stage 08. No runtime acceptance is implied.
