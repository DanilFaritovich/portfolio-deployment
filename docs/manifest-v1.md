# Application manifest v1

Stage 02 implements an offline contract, not deployment. One logical application
has one `registry/applications/<id>.yaml` document; future stages will map it to
one independent Compose project and each component to one Compose service.
The registry currently contains no real applications. Test images/digests and
`.example.test` hostnames in [fixtures](../tests/fixtures/valid) are synthetic;
they neither assert GHCR availability nor approve production routing.

The normative closed JSON Schemas are [Application](../schemas/application-v1.schema.json)
and [AdmissionPolicy](../schemas/admission-v1.schema.json), Draft 2020-12.
Every object rejects unknown fields. There are no Compose fragments, command
strings, environment values, Docker arguments or arbitrary runtime overrides.

## Application fields

| Field | Contract |
| --- | --- |
| `apiVersion`, `kind` | Exactly `portfolio.deployment/v1`, `Application` |
| `metadata.id` | Lowercase hyphen-separated alphanumeric ID, starts with a letter, 1–32 characters; matches filename |
| `metadata.sourceRepository` | GitHub `owner/repository`, no URL; exact match to policy |
| `spec.enabled` | Required boolean; disabled entries receive all checks and reserve IDs/hostnames/aliases |
| `spec.components` | 1–32 components; unique names with the same syntax as application IDs |
| component `image` | Full lowercase `ghcr.io/owner/image@sha256:<64 lowercase hex>`; no tags, including tag-plus-digest |
| component `platform` | Exactly `linux/amd64` in v1; also explicitly allowed by policy |
| component `type`, `health` | `http` requires integer `port` 1–65535 and `health: {strategy: http, path: /health}`; `worker` forbids `port` and requires `health: {strategy: imageHealthcheck}` |
| component `dependsOn` | Optional unique component-name list; existing targets only, no self-dependency or cycles |
| `spec.ingress` | Optional single object: approved lowercase DNS `hostname`, HTTP `component`, matching integer `port`; no wildcard, IP, path routing or Caddy directives |
| `spec.storage` | Required `{type: stateless}` or `{type: persistent, volumes: [{name: data}]}` |
| component `volumeMounts` | Optional list of `{volume: data, mountPath: /app/data}`; named declared volumes only; no duplicate volume or path within a component |
| `spec.configRefs`, `spec.secretRefs` | Optional unique lists of symbolic IDs; component lists of the same names may select only declared references |

Reference names have the same syntax as IDs; they are not file paths, secret
values, environment-variable bindings or delivery instructions. Unused references
are allowed; resolution, existence, versioning and injection are deferred.
Only non-root canonical absolute mount paths with alphanumeric, underscore,
hyphen and interior dot segments are accepted; no `.` / `..`, repeated slashes,
trailing slash or socket/system paths under `/dev`, `/proc`, `/sys`, `/run`,
`/var/run`. All named volumes must be used. A named volume may be shared by
components within an application. No host paths or external volume identifiers
are accepted. Storage declarations never authorize deletion.

Readiness paths are origin-relative absolute HTTP paths, including `/`; optional
final slash is allowed, queries, fragments, percent escapes, dot segments,
whitespace and control characters are forbidden. Health timing, retries and
image healthcheck inspection are deferred; declarations do not prove readiness.

Ingress DNS labels are ASCII lowercase, 1–63 characters, without leading/trailing
hyphens. At least two labels and an alphabetic final label are required, with a
253-character total limit. Policy establishes eligibility, not DNS ownership.

Future networking uses a private application network and only the ingress
component joins the proxy network. Components use their names internally;
`<application-id>-<component-name>` is the future global proxy alias. The validator
reserves aliases across all components, rejects collisions and limits them to
63 characters. No network is created here. Review shared bridge lateral access
before runtime integration. Each component is single-instance in v1.

## Admission policy

[policy/admission.yaml](../policy/admission.yaml) has the same `apiVersion` and
`kind: AdmissionPolicy`. Stage 09.1 proposes narrow ProjectRouter eligibility;
the real application registry remains empty until artifact evidence is reviewed.
Each entry requires:

- `id`, `sourceRepository`: exact application identity and source.
- `imageRepositories`: map from component name to 1–16 permitted GHCR repository
  strings, without digest or tag. At least one component mapping is required.
- `hostnames`: zero or one approved hostnames; empty means internal-only.
- `platforms`: `[linux/amd64]`.

There are at most 128 policy entries, unique IDs and unique hostnames. Policy may
approve applications/components that are not yet registered. A manifest cannot
extend its own permissions. Any policy change requires separate security review;
reviewer authorization, branch protection and CI enforcement are deferred to
later stages. Offline validation cannot prove that a reviewer is authorized.

An optional closed `staticSite` object requires `component`, integer `port`, safe
`readinessPath` and `buildOrigin` (HTTPS hostname only). Its image-map component
key and sole admitted hostname must match. Admitted manifests must contain exactly
that HTTP component/health/ingress, stateless storage and no runtime references,
dependencies or mounts. It constrains declarations even when disabled; artifact
origin/provenance and release authorization need independent evidence. See
[production contracts/security](production-contracts.md) and the
[inactive template](templates/project-router.yaml). This is an additive admission
constraint; it does not relax existing validation or grant execution rights.

OCI image-manifest and image-index digests share the same accepted syntax.
Existence, access, provenance, index contents and platform compatibility require
future deployment preflight. Third-party infrastructure images require an
explicitly approved GHCR source. Real ProjectRouter registration waits for a
verified digest built for `https://danil-miftakhov.dev`; the confirmed production
hostname is `danil-miftakhov.dev`. Synthetic fixtures must not be promoted into the real registry.

## Validation and API

`make validate PYTHON=.venv/bin/python` validates the complete registry.
`make check PYTHON=.venv/bin/python` runs bootstrap checks, validation and pytest.
`make fix` normalizes project-owned text and preserves vendored standards.
Negative YAML fixtures are excluded only from bootstrap YAML parsing, not text
normalization, and are rejected by dedicated tests.

The validator reads no environment secrets and calls no subprocess, Docker, GHCR,
DNS or VPS. CLI success exits 0; failure exits 1 with sorted, deduplicated errors.
Errors identify document/field/rule without echoing input values. It returns no
partially validated registry. All input declarations are checked, including
`enabled: false`; absence/disable does not imply stopping or deleting resources.

```python
from pathlib import Path
from scripts.validate_registry import RegistryValidationError, validate_registry

try:
    registry = validate_registry(Path("/path/to/registry-root"))
    applications = registry.applications  # tuple of owned validated dictionaries
except RegistryValidationError as error:
    errors = error.errors  # sorted tuple of diagnostics; no partial result
```

The root contains `registry/applications` and `policy/admission.yaml`. Schemas
always come from the validator installation; an alternate root cannot substitute
them. API calls do not write files. The result is not a deployment authorization,
a plan, an observed-state model or an immutable deep-frozen object.

Validation bounds are 128 application files, 64 KiB per YAML document (including
policy), 2 MiB aggregate YAML input, 32 collection levels and 4096 YAML nodes per
document. Limits apply before object construction/schema/semantic validation.
Files must be regular, non-symlinked `.yaml` entries; registry/policy directories
must exist and cannot be symlinks. No recursive traversal is performed; nested
entries and unexpected extensions fail. An empty regular `.gitkeep` is ignored.
UTF-8, one mapping document, string mapping keys, no duplicates, aliases, anchors,
merge keys, explicit tags or directives are required. The version is checked
before schema validation, followed by semantic/global checks and admission.
Python/PyYAML/schema validation is deliberately bounded; this is not an OS sandbox
for processing unrestricted inputs. Trusted installed schemas are validated
locally; no remote schema references are resolved.

Python was chosen for existing repository tooling and a small reusable Stage 03
input API; PyYAML parses constrained YAML, jsonschema checks Draft 2020-12, and
pytest covers success/failure boundaries. This choice does not select the future
controller language, execution model, deployment trigger or journal store.

## Optional runtime security v1

Stage 09.3 adds closed `components[].runtimeSecurity` with its own
`apiVersion: portfolio.runtime/v1`. Existing manifests remain compatible when it
is absent. All fields in the object are required when present: non-root `user`,
`readOnlyRootFilesystem`, `capDrop: [ALL]`, `noNewPrivileges: true`, supported
`restart` (`no`, `on-failure`, `unless-stopped`), `imageHealthcheck: preserve` and
`tmpfs` (zero or one bounded `/tmp` mount). Unknown Compose/security fields remain
forbidden. See [schema](../schemas/runtime-security-v1.schema.json),
[ProjectRouter template](templates/project-router.yaml) and
[runtime/operator contract](production-integration.md#runtime-security-v1).
The observed runtime supports the same shape; security drift is an update, not a
no-op. Production preview requires explicit security; actual compatibility and
safe references remain separately verified blockers.
