# C4.4 — Real dependencies and installation packaging candidate

Prepared 2026-10-24. **Not installable / productionTrusted=false.** No VDS/SSH,
Docker, systemd, DNS, infrastructure mutation, approval or signature issuance
occurred. The ZIP is real; independent production approval is absent.

## Sources and observed CI

PR [#59](https://github.com/DanilFaritovich/portfolio-deployment/pull/59) is merged.
Tooling starts from develop `7175b907b4f636805cbd56574982a06b46dd17c9`;
[push CI](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40048261685)
and [source App smoke](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40048261690)
passed. Main remains `02e22f9b3e05043517ba26c0d9637e8ae70de9b8`, with successful
[exact-SHA push CI](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40044410838)
and [Caddy publication](https://github.com/DanilFaritovich/portfolio-deployment/actions/runs/40044410916).
These current API observations do not constitute full source/installation approval.

The candidate retains that **main SHA**. Corrected export/parser tooling from PR
#58 includes the release's existing `scripts/caddy_producer.py` and three exact
`images/caddy` inputs. All **71 exported source files** match exact main Git blobs.
No develop runtime source is substituted. Consequently the candidate still uses
`/usr/sbin/ip`: PR #59's `/usr/bin/ip` correction is absent from this release.
A separately authorized protected-main release of C4.1/C4.3 fixes, followed by
fresh selection, rebuild and review, is mandatory before production installation.

Images remain unchanged:

- Caddy: `ghcr.io/danilfaritovich/portfolio-caddy@sha256:6996e8f98c9a7876e73e362ac8b74990ba13915d97aad30b13af2be45e463d05`.
- ProjectRouter: `ghcr.io/danilfaritovich/project-router@sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853`.

## Actual artifacts and verification

Local directory: `/tmp/c44-installation`. Temporary files are not a durable backup
or a published release. Preserve reviewed bytes/receipts off-host before relying
on them. No wheel/binary/ZIP is committed to Git.

| Local artifact | Actual result |
| --- | --- |
| `wheelhouse/` | Seven binary wheels; SHA256 verified against PyPI HTTPS release metadata |
| `vendor-reviewed/` | 129 regular files, including native modules, schema resources, typing data, SBOMs and licenses |
| `vendor-repeat/` | Repeated export with identical per-file hashes and exclusion report |
| `caddy/caddy` | Official static linux/amd64 2.11.7; fresh cosign upstream signature verification |
| `system-tools/gh` | Official 2.102.0 linux/amd64; archive pin checked before extraction/execution |
| `system-tools/trusted-root.jsonl` | Fresh credential-free GitHub/Sigstore TUF update in empty HOME, 34634 bytes |
| `runtime-files.json` | Complete 130-file asset inventory including Caddy |
| `bundle.zip`, `bundle-repeat.zip` | 57217927 bytes each, byte-for-byte identical, 201 inventoried files |
| `candidate/` | Parsed/extracted local candidate; never installed |

Whole ZIP SHA256:
`183aefade767fba4fd85ed4e9e68ad93ed46d35b17a42146b7828d5f63466408`.

Committed **comparison evidence**, not production pins:
[wheel lock](../artifacts/c4.4/wheels.json),
[requirements](../artifacts/c4.4/runtime-requirements.lock),
[vendor hashes/exclusions](../artifacts/c4.4/vendor-export.json),
[runtime inventory](../artifacts/c4.4/runtime-files.json),
[bundle inventory](../artifacts/c4.4/bundle-files.json),
[candidate result](../artifacts/c4.4/candidate-report.json), and
[tool preparation](../artifacts/c4.4/tool-preparation.json).
Raw PyPI metadata and Caddy verification output remain beside local artifacts.

All **57 exported runtime modules** imported from the candidate under separately
downloaded uv-managed **CPython 3.12.3 -I -S -B**. Native PyYAML CLoader, native rpds,
JSON Schema validation/schema resources and Caddy `load_lock()` passed. Local
libc was **glibc 2.43**, not Ubuntu 24.04: Ubuntu execution was not performed.
Both native wheels declare cp312-cp312 manylinux2014/2.17 x86_64 compatibility;
ELF inspection found highest GLIBC symbol 2.14 in each. rpds needs libgcc_s,
libpthread, libc and the ELF loader; PyYAML needs libpthread/libc. This supports
portability but does not prove the VDS library inventory or protected metadata.
Repeat isolated imports in a reviewed Ubuntu 24.04/Python 3.12 environment before
production acceptance. Do not vendor the standalone interpreter as Ubuntu Python.

## Reproducible workstation procedure

Use the reviewed C4.4 tooling checkout and pinned development environment.
Runtime closure is PyYAML 6.0.3, jsonschema 4.26.0, attrs 26.1.0,
jsonschema-specifications 2025.9.1, referencing 0.37.0, rpds-py 2026.9.1 and
**typing-extensions 4.15.0**, required by referencing's Python <3.13 marker.
No jsonschema extras or development dependencies are included. Five wheels are
pure Python; two native wheels are cp312. No Python 3.14 site-packages are copied.
PyPI JSON SHA256 and separately downloaded bytes agree; no publisher attestation
or independent administrator endorsement is claimed. Metadata response hashes
record observed responses, which may change without changing immutable wheel bytes.

```sh
C44_OUT=/absolute/new/c44
mkdir -m 700 "$C44_OUT"
mkdir -m 700 "$C44_OUT/wheelhouse"
# Explicit public downloads, only on the workstation.
.venv/bin/python -m pip --isolated download --no-cache-dir \
  --index-url https://pypi.org/simple --only-binary=:all: --require-hashes \
  --no-deps --platform manylinux2014_x86_64 --python-version 3.12 \
  --implementation cp --abi cp312 --dest "$C44_OUT/wheelhouse" \
  -r artifacts/c4.4/runtime-requirements.lock
# Offline export, with an absent output directory.
.venv/bin/python -m scripts.prepare_installation_vendor \
  --wheelhouse "$C44_OUT/wheelhouse" --output "$C44_OUT/vendor" \
  > "$C44_OUT/vendor-export.json"
```

The exporter verifies wheel SHA256, target tags, METADATA against the release
metadata lock, every original RECORD entry/hash/size and full target marker/version
closure before writing. It omits exactly bundled tests/benchmarks and regenerates
RECORD deterministically, keeping other data/licenses. It rejects bytecode, `.pth`,
symlinks, unsafe paths, collisions and excessive sizes. No wheel code, installer,
source build, network or resolver executes. Workstation `packaging` audits metadata;
it is not a runtime dependency. Repeat export to another absent directory and
compare reports. There is **no pip installation on the VDS**.

Acquire official Caddy archive/certificate/signature/cosign using fixed URLs/pins
from `images/caddy/inputs.lock.json` and existing `checked_download`. Reuse
`scripts.caddy_producer.upstream_command` to authenticate issuer
`https://token.actions.githubusercontent.com`, release workflow at v2.11.7,
source `72dd0fb067f6d7826c7f79907670ba4a713bfe37`, tag and push event.
Then use existing `extract_binary` to enforce the binary pin/static ELF and extract
only Caddy/LICENSE. C4.4 repeated this using existing downloads freshly hash-checked
against the lock and an empty verifier HOME. This standalone executable check does
not authenticate the separate Caddy container image. Reproduction, with
explicit public downloads and fresh paths:

```sh
.venv/bin/python - "$C44_OUT" <<'PY'
import sys
from pathlib import Path
from scripts.caddy_producer import checked_download, extract_binary, load_lock, run, upstream_command
root = Path(sys.argv[1])
assert not (root / 'caddy').exists() and not (root / 'LICENSE').exists()
directory = root / 'caddy-acquisition'
directory.mkdir(mode=0o700)
home = directory / 'home'
home.mkdir(mode=0o700)
lock = load_lock()
cosign = directory / 'cosign'
checked_download('https://github.com/sigstore/cosign/releases/download/v' +
                 lock['cosignVersion'] + '/cosign-linux-amd64', lock['cosignSha256'], cosign)
cosign.chmod(0o500)
base = 'https://github.com/caddyserver/caddy/releases/download/v' + lock['version']
base += '/caddy_' + lock['version'] + '_linux_amd64'
for suffix, name, key in (('.tar.gz', 'caddy.tar.gz', 'archiveSha256'),
                          ('.pem', 'caddy.pem', 'certificateSha256'),
                          ('.tar.gz.sig', 'caddy.sig', 'signatureSha256')):
    checked_download(base + suffix, lock[key], directory / name)
run(upstream_command(cosign, directory, lock), env={'HOME': str(home), 'PATH': '/usr/bin:/bin'})
extract_binary((directory / 'caddy.tar.gz').read_bytes(), lock, root)
PY
```

```sh
.venv/bin/python - "$C44_OUT" <<'PY'
import hashlib, json, sys
from pathlib import Path
from installation.operator_bootstrap import safe_name
root = Path(sys.argv[1])
pins = {}
for p in sorted((root / 'vendor').rglob('*')):
    assert not p.is_symlink() and (p.is_dir() or p.is_file())
    if p.is_file():
        name = 'vendor/' + p.relative_to(root / 'vendor').as_posix()
        safe_name(name)
        pins[name] = hashlib.sha256(p.read_bytes()).hexdigest()
assert not (root / 'caddy').is_symlink()
pins['tools/caddy'] = hashlib.sha256((root / 'caddy').read_bytes()).hexdigest()
with (root / 'runtime-files.json').open('x') as stream:
    stream.write(json.dumps(pins, sort_keys=True, indent=2) + '\n')
PY
git --no-replace-objects rev-parse --verify 02e22f9b3e05043517ba26c0d9637e8ae70de9b8^{commit}
.venv/bin/python -m scripts.production_integration.install_bundle \
  --revision 02e22f9b3e05043517ba26c0d9637e8ae70de9b8 \
  --vendor "$C44_OUT/vendor" --caddy "$C44_OUT/caddy" \
  --dependency-pins "$C44_OUT/runtime-files.json" --output "$C44_OUT/bundle.zip"
# Repeat with another absent --output and cmp the ZIP bytes.
sha256sum "$C44_OUT/bundle.zip"
```

Keep the shipped blocking binding for this candidate; do not pass `--bindings`.
Production requires an independently reviewed actual binding and its independent
pin, then a rebuild against a newly selected corrected protected-main release.

Before extraction, use existing `installation.operator_bootstrap.read_bundle(raw)`:
it checks every SHA256, complete inventory, bounds, names, modes and unit contract.
C4.4 additionally compared all 71 source Git blobs and all 130 external asset hashes
with no missing/extra files. After source review, write only parsed files to a fresh
local directory, then run:

```sh
/absolute/reviewed/python3.12 -I -S -B artifacts/c4.4/check-imports.py \
  /absolute/reviewed/extracted-candidate
```

**Independent verification remains blocked.** The real default
`ManagerBundleVerifier().verify(raw)` rejected this ZIP with
`production-manager-independent-review-unconfigured`. No fake GitHub port,
source policy, binding, authority or successful verifier result was supplied.
Local comparisons and import tests are not independent production verification.

On a separate reviewed workstation, establish real authenticated `GitHubRead`,
`WorkflowCheck('DanilFaritovich/portfolio-deployment', 'Offline checks', 15368,
'.github/workflows/offline.yml')`, independently reviewed complete `asset_pins`
and actual fixed `bindings_pin`. Instantiate existing `ManagerBundleVerifier`
**outside the candidate**, then call `verify(raw)`: protected-main policy B,
exact-SHA push CI, complete tree/inventory/content, followed by repeated source
checks. Review wheel provenance and export transformations/exclusions independently
before adopting asset pins. A successful source/bundle review still grants no privilege.

## System tools and Infrastructure handoff

| Tool | Known version / bytes / origin | Minimal remaining action |
| --- | --- | --- |
| Python | C4.2 reports 3.12.3; `/usr/bin/python3` may be a symlink; resolved file hash unknown | Fresh resolved interpreter/ABI/stdlib/native closure, Ubuntu package provenance and protected metadata; pin resolved executable, not symlink spelling |
| Docker | C4.2 reports 29.9.0; `/usr/bin/docker` hash/origin unknown | Verify exact binary/package, daemon ID/socket/config, Engine/Compose syntax compatibility |
| Compose | C4.2 reports 5.6.0; plugin identity unknown | Observe four fixed plugin directories; exactly one regular plugin, hash/package/metadata; no automatic duplicate removal |
| ip / ss | Corrected `/usr/bin/ip`, `/usr/bin/ss`; reported `/usr/sbin/ip` symlink unsuitable | Fresh iproute2 package/hash/one-link/root/protected ancestry; corrected protected-main source |
| ssh-keygen | Fixed `/usr/bin/ssh-keygen`; host bytes/version unknown | Verify OpenSSH package, bytes and library closure; retain SSHSIG gates |
| gh | Missing on VDS; locally verified 2.102.0 | Separately authorized Infrastructure install and fresh host hash/version/metadata |
| Caddy validation | Locally authenticated 2.11.7 | Reviewer adopts verified provenance/pin for fixed `tools/caddy` |
| Libraries / trust roots | Host inventory absent; local fresh TUF snapshot available | Verify actual stdlib/loader/native libraries/OS CA roots, loading/search paths, TUF freshness/origin; pin complete protected files |

Local gh archive SHA256:
`bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386`;
executable `7469124f706944133d6a169691dd1c6c3511b12e85878d255e044e2948df4c9b`.
The archive matched both existing Manager pin and official cli/cli release API
asset digest. Caddy binary SHA256:
`678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`.
TUF snapshot SHA256:
`65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c`.
A dated local TUF result is not permanent freshness or installed policy evidence.

Version, file hash and independently authenticated origin are distinct requirements.
`SealedInstallation.protected` additionally requires root ownership, no group/world
writes, one-link regular files and protected ancestry. **All current VDS pins are
unknown**. Inspect interpreter/extension ELF NEEDED/version information, imported
stdlib files and authenticated OS package manifests; review actual resolved regular
files and loading/search configuration. Include TLS CA roots and all authority/helper
code. No automatic library collector or protection bypass is added. Package updates
invalidate pins and require reviewed maintenance.

Read-only inspection of sibling `portfolio-server-infrastructure` found guarded
package-management roles/playbooks (`roles/docker_host`, `roles/server_operations`),
but **no gh role/target**. Its `make setup` prepares workstation tools; its VM QEMU
setup is not VDS provisioning. Do not misuse these targets for gh. No sibling file
was modified.

Minimal proposed Infrastructure change: separately review/authorize exact gh
2.102.0 acquisition and installation within its existing administrative process.
Reuse existing `scripts.application_acceptance.provision_gh` archive validation and
[official release metadata](https://github.com/cli/cli/releases/tag/v2.102.0).
For standalone preparation use its supported `execute` callback with a minimal
environment and bounded subprocess; the default callback expects acceptance-only
Docker configuration. The callback is workstation tooling, not an installed authority.
Install only the authenticated extracted regular executable at `/usr/bin/gh`,
root-owned and non-writable by others. Do not add an unpinned APT repository, upgrade
unrelated packages or alter access policy. Verify version/hash/one-link metadata,
protected ancestry and libraries afterward. This is a proposal, not an executed
command or Manager install target. If Infrastructure chooses a Debian package,
authenticate that exact package/binary/dependency inventory separately; its rebuilt
binary cannot inherit the tar executable hash by assumption.

## Remaining blockers and shortest path

Reuse the [first-install readiness contracts](first-production-installation-readiness.md)
and [existing installer](production-installation.md). No new installer, engine,
daemon, approval service or deployment stage is needed.

| Implemented component | Actually absent | Owner decision / fresh evidence | Minimal closing action |
| --- | --- | --- | --- |
| Git exporter, independent source verifier, installer | Corrected protected-main release; real independent bundle review | Maintainer release authorization; reviewer establishes source/assets/binding policy | Publish reviewed fixes through protected main, select exact SHA, rebuild twice and run real independent verifier |
| InitialInstallationApproval and standalone no-argv helper | Working admin binding and human one-install decision | Existing portfolio_admin channel; actual machine ID and exact helper/binding/bundle/external inventory tuple | Independently review fixed code embodying that human decision; no JSON/env approval shortcut |
| SealedInstallation, composition.build, ProductionExecutor | Fixed phase wiring; prerequisites/bootstrap/public-postcondition production authorities | Owner chooses real authorities/freshness/revocation; actual host/policy inputs | Implement/review only concrete fixed adapters over existing ports; no test callbacks or invented post-bootstrap IDs |
| HostPolicy, PolicyHostContracts, LocalHostSources | Approved host policy and authenticated prerequisite evidence | Fresh full IPv4/IPv6 routes/rules/addresses/listeners, Engine inventory/IPAM, protected storage/mounts/capacity/inodes; decide CIDRs/budgets | Separately authorized read-only evidence and independently bound exact phase policy/host |
| SourceTrust, LocalSourceAttestor, ProductionStateReader | Trusted composition over actual observation ports | Fresh payload/round/machine/daemon-bound samples | Bind exact objects and authorities; historical reports never become attestations |
| ReleaseVerifier, collectors, SSHSIGVerifier | Source/crypto ports; operator identity/trust inventory/custody | Readable authenticated source policy; owner chooses separate off-VDS signing key/principal, passphrase, backup, rotation/revocation | Pin actual gh/root/ports and public trust file; no private key on VDS, key or approval issued here |
| Recovery gates, journal/LKG | Backup/restore/console/containment proof | Owner names rebuild operator/budget/access and backup/key custodians; accepts RPO/RTO; fresh provider drill | Test encrypted off-host backup and isolated restore of manager/journal/ledger/LKG/certificates; test provider stop/isolation without SSH; rebuild choice alone satisfies no gate |
| ProjectRouter ImagePreparation and Caddy release verifier | Approved actual caches and Caddy acquisition binding | Exact-digest provenance/runtime acceptance; separate acquisition decisions | Existing ProjectRouter candidate/SSHSIG/ledger path; separate reviewed Caddy acquisition, never widen ProjectRouter pull scope |
| ManagedBootstrapPort, ACTIVE channel, gateway postconditions | Created resource/socket IDs and public HTTPS evidence | Fresh network/container IDs, UDS inode/peer, storage/image/config identities after bootstrap; actual DNS/TLS/path | Independently approve rollout-phase wiring over observed resources and real public postconditions |

1. Complete corrected protected-main release, gh Infrastructure handoff and separately
   authorized fresh read-only VDS evidence; decide host/recovery/key policy and
   implement/review actual fixed phase adapters/bindings.
2. Rebuild with vendor/Caddy and real binding; independently verify full source/assets/
   whole ZIP, then administrator approves the exact installation tuple.
3. **Separate initial-install permission:** authenticated protected intake of pinned
   helper/admin binding/bundle; existing no-argv helper; verify installed files,
   storage and lock. No systemd enable/start or image action is implied.
4. **Separate acquisition permissions:** authenticate/cache exact Caddy and ProjectRouter
   digests through their distinct supported paths; preserve receipts.
5. **Separate bootstrap permission:** approved bootstrap policy, fresh SSHSIG and
   explicitly authorized unit recognition/start/intake; existing executor validates
   empty config, creates approved resources, starts pinned Caddy, verifies twice,
   and records bootstrap LKG.
6. Reobserve created IDs/ACTIVE socket, approve rollout wiring/inventories through
   reviewed phase handoff (no in-place pinned-code edits), then separately authorize
   first ProjectRouter rollout. Internal readiness and real public
   `https://danil-miftakhov.dev/en/` DNS/TLS/path checks gate LKG. DNS/firewall/provider
   changes need their own authorization.

C4.2 Ubuntu/tool/SSH observations are historical owner-supplied facts, not fresh
production attestations. Nothing here authorizes installation or those operations.
