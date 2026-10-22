# Runtime Contract Verification audit

Audit base: `418adbeac5f7928f8936097e0e4835578314f990` (`develop`, PR #21).
Evidence reviewed on 2026-10-22: [run 39833342324](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39833342324), artifact
`application-acceptance-39833342324-1` (ID `12450121571`).

## Architecture findings

The trust boundaries were substantially correct and are preserved:

1. `production_integration/artifacts.py` authenticates OCI bytes/platform mapping
   and binds Engine image inspection. Source/provenance/attestation verification
   remains separate. Container labels cannot establish image or source trust.
2. Container identity combines discovery, ownership and stable container ID across
   readiness samples. `Config.Image` identifies the requested reference, while
   container `Image` must equal the already verified Engine image ID. Neither may
   substitute for the other or for an OCI manifest digest.
3. `compose.py` projects the admitted component into runtime settings. Image defaults
   remain the expected command/environment/healthcheck unless an explicit supported
   manager override (currently `user`) applies. OCI parameters are defaults for
   runtime creation, not an expectation that every container JSON field is byte
   identical to image JSON. [OCI configuration](https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md)
4. Runtime security verifies effective configuration independently of ownership.
   Isolated acceptance additionally reads kernel process capabilities/UID/GID and
   mount flags. That independent corroboration is useful, not a competing Compose
   contract. Readiness is a separate behavioral assertion.

Both isolated acceptance and production discovery already called
`docker_discovery.container_runtime()`. VM E2E uses it too. The defect was inside
that shared policy, not a second isolated-acceptance implementation. The new
`runtime_contract.verify_runtime()` is its single structured implementation;
boolean facades and acceptance diagnostics consume that exact result.
`runtime_security.differing_fields()` owns the security subset for both its old
boolean interface and the structured verifier. No producer/admission boundary moves.

## Necessary assertions and representation boundaries

| Group | Required invariant | Representation handling |
| --- | --- | --- |
| `image` | Verified upstream evidence, exact reference, exact bound image ID | No tag/digest/ID substitution or normalization |
| `config` | Image command, entrypoint, env, working directory, exposed ports, volume declarations, healthcheck; admitted User | Typed optional empty strings/collections; healthcheck optional numeric zero; command/env order and nonzero health settings remain exact |
| `security` | Root filesystem policy, capabilities, no-new-privileges, restart, no privilege/host namespace/device/port/bind escape | Two exact no-new-privileges spellings; typed Go zero defaults; no unknown security options |
| `networks` | Exact endpoint set, required aliases, only bounded Compose/Engine aliases | Any declared endpoint may be primary; fixed empty IPAM fields normalize; static/link-local IP requests and foreign aliases remain blocked |
| `mounts` | Exact named volumes/targets/RW and tmpfs targets/options; no extra mounts | Legacy tmpfs need not be duplicated in top-level Mounts; any supplied metadata must corroborate it; named-volume requests enforce nocopy |
| `engine` | Supported runtime and resource/DNS/group/init/autoremove settings | Only field-specific typed zero/null settings and the supported runc value; unsupported nonzero overrides still block |

Compose automatically supplies container-name/service aliases, selects a primary
network, constructs Config and maps service tmpfs into HostConfig.Tmpfs. Those
Engine values must be compared semantically, not against a single assumed network
ordering or a fictional inspect shape. Alias permissions remain bounded to the
service, discovered container name/short ID and explicitly declared aliases;
this audit established no alias bug in the historical run.
[Compose create implementation](https://github.com/docker/compose/blob/v2.40.3/pkg/compose/create.go)

Moby merges image defaults into Config; inheritance does not authorize a changed
User, Cmd, Entrypoint, Env or disabled healthcheck. Optional zero healthcheck
members are equivalent to omitted members, but explicit nonzero changes are still
rejected. Engine runtime health scheduling defaults are not blindly substituted
into inspect Config. [Moby Config merge](https://github.com/moby/moby/blob/v28.5.1/daemon/commit.go)

Generated hostname, labels, endpoint IDs/addresses, host volume source paths and
attach-output metadata are not image identity assertions. API output metadata is
not treated as arbitrary permission to introduce new runtime settings. Unknown
healthcheck/IPAM/volume-option settings fail closed. This is a bounded supported
runtime projection, not a universal validator for every future Engine API field.
Host defaults that impose unsupported nonzero resource/DNS/ulimit settings remain
explicit blockers requiring a separately reviewed policy, rather than guessed
normalizations.

## Established defect versus historical cause

The artifact confirms Engine binding, Compose startup, discovery, ownership and
container identity passed, then runtime verification failed on probe 1; cleanup
passed. It records neither failing runtime field nor inspect data. Container state
was still `unknown` because the old code checked it later. **The exact first mismatch
in that historical invocation cannot be proven from the retained artifact.**

A systemic false rejection is independently proven on a real Engine:
`HostConfig.Tmpfs` contains the requested tmpfs, while top-level `Mounts` has no
tmpfs entry. The old code required those destination sets/counts to match and
therefore rejected this correct representation. Moby implements legacy
`HostConfig.Tmpfs` separately from `Container.MountPoints`; inspect's `GetMountPoints`
iterates only the latter. [Moby tmpfs and mount-point implementation](https://github.com/moby/moby/blob/v28.5.1/container/container_unix.go)

The fix retains exact tmpfs target and option checks, rejects duplicate option
names, unexpected mounts and inconsistent metadata, and accepts the absence of
redundant tmpfs metadata. It does not permit insecure or unbounded tmpfs.
Docker exposes separate `--tmpfs` and `--mount type=tmpfs` forms with different
option handling. [Docker tmpfs documentation](https://docs.docker.com/engine/storage/tmpfs/)

Other fixes are typed empty User/WorkingDir/StopSignal handling, optional zero
healthcheck members, a primary NetworkMode constrained to the exact declared
endpoint set, and empty EndpointIPAMConfig structure handling. The IPAM default
was encountered by the new real test; the previous verifier did not inspect that
field. It is not claimed as a cause of the original failure. Existing collection,
restart and no-new-privileges equivalences are retained and checked more strictly
for JSON types. Malformed values such as boolean numeric limits are not defaults.

The old tmpfs metadata equality duplicated the security check while asserting an
incorrect representation. Some other truthiness/set comparisons were too weak:
they obscured wrong JSON types and duplicate mount/options entries. The new policy
removes that contradictory representation requirement and strengthens those
boundaries. No admission, immutable ProjectRouter digest, OCI/provenance checks or
production registry changes are included.

Acceptance now classifies a stopped container before runtime verification. An
exited process with missing endpoints produces `container-not-running`, including
bounded exit/OOM evidence, instead of a misleading configuration mismatch.

## Reusable real integration test

Run only inside a disposable Linux/amd64 VM or disposable GitHub-hosted runner
with Docker Engine, Compose, `cc` with static libc, Python and pinned development
dependencies installed. The daemon must have no containers. Explicit local socket
and authorization are mandatory; Docker contexts/credentials/proxy configuration
are not inherited:

```sh
make runtime-integration-test PYTHON=.venv/bin/python \
  RUNTIME_TEST_ENDPOINT=unix:///var/run/docker.sock \
  RUNTIME_TEST_AUTHORIZE=disposable-runtime-integration
```

The harness statically compiles `tests/runtime_fixture/fixture.c`, imports a local
scratch rootfs, renders session-scoped Compose, and compares actual Engine inspect
against the shared policy. It uses no producer image or registry. Local fixture
ImageEvidence is restricted to this harness and cannot grant production admission.

Only the correct configuration runs. Negative cases use real Compose **create**,
without start: even the host-port case never binds a port and privileged/capability
variants never run. They check actual Engine-generated configuration, not mutated
inspect mocks. Wrong-image-ID testing compares the real valid inspect with an
intentionally incorrect expected binding. Named volumes and two internal networks
exercise genuine mount/alias metadata. Cleanup inventories by unpredictable
session label, re-inspects ownership, removes only owned disposable resources,
uses no down/prune, and checks for leftovers. An intentional exception after real
create exercises the finally cleanup path.

The separate `runtime-integration.yml` PR workflow runs on `ubuntu-24.04` with
contents-read permission, no stored credentials/secrets or registry publication.
The existing Application Acceptance workflow remains dispatch-only.

## Validation evidence and limits

Real local execution: new Multipass Ubuntu 24.04 VM
`pd-runtime-225a0c8f013aaea8`, Engine **29.1.3**, Compose
**2.40.3+ds1-0ubuntu1~24.04.1**. **19/19 checks passed**, cleanup passed:
valid runtime, incorrect expected image ID, Docker-managed defaults,
User/Entrypoint/Cmd/Env/Healthcheck drift, privileged, added capabilities, missing
capability drop, security options, writable root, port bindings, tmpfs changes,
restart policy, extra network, extra mount, and cleanup after injected failure.
The final safe observations were `tmpfsConfiguredInHostConfig=true`,
`tmpfsPresentInMounts=false`, `primaryNetworkDeclared=true`. Final daemon reads
showed no containers or fixture images. The VM was stopped; its disk is retained.

These results prove the bounded fixture contract, not ProjectRouter startup,
source trust, arbitrary Engine versions, production availability or rollout.
The owner must run protected-branch isolated acceptance after merge. No VDS/SSH,
production deployment, acceptance dispatch, release or merge was performed.

Offline validation: `make fix` and targeted acceptance/discovery/security tests
passed. Final `make check PYTHON=.venv/bin/python`: **1496 passed**.
`make acceptance-check`, `make workflow-check` with pinned actionlint 1.7.7 and
`git diff --check` passed. Offline results are separate from the real VM results.
