# Stage 09.4B.1 — Isolated Caddy runtime acceptance

This is disposable image acceptance, not production deployment or admission.
No SSH/VDS, production Docker, DNS/firewall changes, production ACME certificates,
production gateway activation, operator approval or automatic CD is involved.

## Selected immutable image

Official stable Caddy **2.11.7**, Alpine variant, `linux/amd64`:

| Identity | SHA-256 digest |
| --- | --- |
| Runtime OCI index | `eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f` |
| Selected amd64 manifest | `04913b1da43339a40ad1ea87827d70a576ac5aa6cf674c6e24fdca675887533a` |
| Image config | `3067cc4e02c091399edbd5d65f85a3ff4d511574aecadb47f280c3761e68852e` |

Runtime reference:
`docker.io/library/caddy@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f`.
`2.11.7-alpine` was used only for initial selection; execution never resolves a tag.
The public [Docker Official Image catalog](https://hub.docker.com/_/caddy),
[official-images mapping](https://github.com/docker-library/official-images/blob/master/library/caddy)
and [source Dockerfile at the catalog revision](https://github.com/caddyserver/caddy-docker/blob/9234f5785658cae8129bfae366e196d0670468ac/2.11/alpine/Dockerfile)
identify the source and build recipe. These are origin evidence, not authenticated
proof that this registry artifact was built by an approved builder.

Raw registry bytes are retained in [fixtures](../tests/fixtures/caddy/image-lock.json).
`.oci` files preserve exact bytes against text normalization. The shared
`map_oci_bytes` verifies SHA-256, descriptor sizes/media types, unique amd64
selection and config platform; existing application `verify_oci_bytes` retains
its GHCR admission restriction. Image pull and fresh Engine binding are additional
real-test gates. Labels or RepoDigests alone do not establish identity/provenance.

Verified config defaults: no Entrypoint, default root UID/GID, working directory
`/srv`, command `caddy run --config /etc/caddy/Caddyfile --adapter caddyfile`,
`CADDY_VERSION=v2.11.7`, `XDG_DATA_HOME=/data`, `XDG_CONFIG_HOME=/config`;
no image healthcheck or declared volumes. Exposed image metadata lists TCP 80/443,
UDP 443 and TCP 2019; EXPOSE does not publish those ports. Production renderer
publishes only TCP 80/443. The isolated runner publishes **no host ports**.
Root here is the existing official gateway contract, not an application UID
exception: effective capabilities must be exactly NET_BIND_SERVICE, with NNP,
seccomp and read-only root. Production ownership remains subject to operator review.

## Provenance blocker

The index references amd64 attestation manifest
`sha256:4910e59aced419e87971a6f43e52ddc436b0d7a90d11cd8dd21b7925ce49e31d`.
Its available statements include SPDX SBOM and SLSA v0.2 provenance. The retained
provenance statement hashes to
`sha256:50dfc88401f131b0fc5e1aeadc786cf1ff9c6dba398cf9d01ca58d7377f05d7f`;
its subject matches the amd64 manifest. It claims BuildKit build type,
builder `https://github.com/docker-library`, source commit
`9234f5785658cae8129bfae366e196d0670468ac`, directory `2.11/alpine`.
Hash linkage authenticates bytes relative to the selected digest, not the builder.
No cryptographic signer/issuer verification was established. Independent authenticated
Caddy provenance approval/verification remains a **production blocker**; runtime
success cannot remove it. No production trust booleans or approval are fabricated.

## Execution boundary and command

Only the [dedicated PR workflow](../.github/workflows/caddy-integration.yml) is
supported. It uses a fresh GitHub-hosted Ubuntu 24.04 x64 runner, `contents: read`,
no production credentials and a non-persisted checkout token. No self-hosted,
workstation, arbitrary socket or remote Docker endpoint is accepted. Required
runner metadata, exact local socket, authorization and empty daemon container
inventory are checked before resource creation. Runner metadata is an execution
guard, not an authenticated production host attestor; do not spoof it locally.

Within that disposable runner only:

```sh
make caddy-integration-test PYTHON=python CADDY_TEST_ENDPOINT=unix:///var/run/docker.sock CADDY_TEST_AUTHORIZE=disposable-caddy-integration
```

`scripts/caddy_integration.py` reuses `CaddyRuntime.render()`, `inspect_caddy_contract`,
the shared OCI mapper/Engine binder and session-owned bounded Docker/cleanup helpers.
It does not introduce a gateway controller, Compose renderer or production write
transport. Production path validation remains unchanged: only the resulting test
projection substitutes temporary paths/project/network identities and removes
host port publication. The internal bridge prevents external connectivity. Test
containers have no Docker socket. Data/config are private temporary bind directories;
no existing volumes, production names, host ports or public domain are used.

The executable fixture uses the existing production loopback admin renderer plus
an exact HTTP-only response on container port 80, with `auto_https off` and no
hostname. Only that exact fixture is permitted to start. The unchanged production
bootstrap and full generated `acceptance.invalid` reverse-proxy candidate receive
official `caddy validate` checks, without activation or TLS issuance. Production
80/443 host binding, DNS, ACME, certificate recovery, TLS handshakes and live gateway
attestation/reload remain outside acceptance.

## Scenarios and evidence

The runner verifies binary version, immutable OCI/Engine defaults, real startup,
Compose/Engine security and bind representations, read-only root/config, PID 1
UID/GID and effective/permitted/bounding capability mask `0x400`, zero ambient
capabilities, NNP and seccomp; HTTP on container port 80; active admin address
127.0.0.1:2019; peer HTTP reachability and admin denial; official validation and
invalid candidate rejection; restart/readiness and persistent `/data`, `/config`
markers plus Caddy autosave. Dangerous Caddy configurations are rejected before
startup by exact fixture restriction. Real negative runtime containers are created
but never started: extra capabilities, absent drop/NNP, writable root/config,
privilege, extra mount and admin publication must fail comparison. Cleanup is also
exercised after an injected partial failure following real Compose creation.

Cleanup re-inspects session ownership before removing exact test containers and
empty networks. It never prunes, uses Compose down, removes preexisting volumes
or deletes the pulled official image. Temporary bind data is disposable and removed
only after harness teardown; the entire runner is ephemeral. Abrupt job cancellation
relies on runner destruction, not a claim that finally always runs.

The bounded JSON report contains fixed stages/codes, case names, approved tool
versions and boolean evidence; never logs, full inspect, config, responses or
exception text. It always reports `productionAdmission: false` and the provenance
blocker. A failed cleanup fails acceptance. The workflow retains the sanitized
report for 30 days; preserve an external audit copy before artifact expiry.

Offline regressions and required Make checks passed during preparation. Real
Docker acceptance is pending the first PR workflow run; update this section only
with its actual result. Existing confirmed ProjectRouter acceptance is separate:
[run 39837965499](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39837965499)
passed at manager revision `6b4173d7c0d51439041a59bd12cc6a6a4d3b161d`.
[main Offline CI 39839861288](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/39839861288)
passed at `65af84bd0de23af9cf9ae40b2c0c411e1fbe2c36`. These do not prove production
admission, current host state, DNS/TLS or rollout authorization.

Production remains unperformed and blocked until Stage 09.4B supplies authenticated
source/runtime/gateway evidence, approved infrastructure execution/storage/network
ownership, Caddy provenance, full actual candidate validation and separate exact
bootstrap/rollout approval. Existing write blockers, disabled ProjectRouter candidate,
image digest and admission policy are unchanged.
