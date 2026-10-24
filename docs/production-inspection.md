# Stage 09.2 — Production VDS read-only diagnostics

Status: implemented and tested offline. **No real VDS inspection or SSH connection
was performed for this stage.** Stage 09.3 adapters/Caddy and production execution
remain separate tasks. Acceptance: **117 new offline tests**, **937 total tests**
passed through `make check`; documentation/link/parity checks also passed. Workflows
were unchanged. Historical Stage 07 VM results do not verify this server;
this document makes no claim that those local VMs still exist.

## Existing infrastructure handoff

The infrastructure repository owns provisioning, users, keys, host trust, Docker
installation and firewall. This layer consumes its local static inventory without
importing its controller, executing playbooks or changing its files.

The adapter was checked against the local infrastructure checkout and GitHub
`develop`: [example inventory](https://github.com/DanilFaritovich/portfolio-server-infrastructure/blob/develop/inventories/production.example.yml),
[Ansible configuration](https://github.com/DanilFaritovich/portfolio-server-infrastructure/blob/develop/ansible.cfg),
[inventory adapter](https://github.com/DanilFaritovich/portfolio-server-infrastructure/blob/develop/scripts/portfolio_cli/inventory.py),
[SSH interface](https://github.com/DanilFaritovich/portfolio-server-infrastructure/blob/develop/scripts/portfolio_cli/ssh.py)
and [operator guide](https://github.com/DanilFaritovich/portfolio-server-infrastructure/blob/develop/docs/operator-guide.md).
Recheck the infrastructure revision/handoff before a real operation.

Supported structure is exactly one host at
`all.children.bootstrap.hosts.<alias>`. Required host fields are `ansible_host`,
integer `ansible_port` (1–65535), non-root `ansible_user` and
`ansible_private_key_file` (absolute or `~/` path). The managed user is selected,
never `bootstrap_login_user`. Existing interpreter, swap and hardening fields are
validated but never used to provision the host. Unknown host fields, group vars,
multiple hosts, dynamic inventory, YAML aliases/duplicates/tags, Jinja, connection
options and password fields are rejected. Existing port/key values are reused;
there is no fallback port, identity override or independent inventory to maintain.
Paths with symlinks, traversal, whitespace, shell syntax or OpenSSH expansion
tokens are unsupported. Inventory bytes/depth/nodes use the existing bounded
registry YAML parser. Private key contents are never read by the Python layer.

## Commands and first authorized connection

Install the existing pinned development dependencies as described in the README.
The local Linux controller needs `/usr/bin/ssh` and `/usr/bin/ssh-keygen`.
From the deployment checkout:

```bash
make production-check PRODUCTION_INVENTORY=../portfolio-server-infrastructure/inventories/production.yml
make production-report
```

`production-check` is entirely offline: it parses inventory, stats the selected
key and trust file, and uses only local `ssh-keygen -F` lookup (including hashed
entries). It performs no SSH, DNS lookup, authentication or network request and
checks report storage permissions/writability without creating directories, and
changes no key, inventory or trust file. Missing/unsafe prerequisites stop with a
blocker. A passed check proves local configuration only, not authentication or
server readiness. Key permissions must exclude group/others; the key and trust
file must be regular, owned by the invoking user, without hardlinks/symlinks.
Their parent directories must be user owned and not writable by group/others.

Before the first **separately authorized** inspection, the operator must:

1. Confirm the intended host, inventory port, managed user, infrastructure revision
   and scope. Preserve the existing automation key; never paste its contents into
   chat, Git, terminal logs or reports.
2. Independently verify the public SSH host key/fingerprint using an authenticated
   provider console, an administrator or an already trusted computer. An entry in
   `known_hosts` does **not** prove how its original trust was established. A key
   obtained over an untrusted scan is not independently authenticated. Use the
   infrastructure project's documented trust export/import process when needed;
   this diagnostic layer never repairs, transfers or accepts trust.
3. Ensure existing `~/.ssh/known_hosts` contains the exact endpoint: `host` for
   port 22, `[host]:port` otherwise. Hashed entries are supported; marked/revoked
   and certificate-authority records fail closed. Resolve rotation/mismatch with
   the infrastructure owner; do not delete records to bypass the check.
4. If the selected existing private key is encrypted, unlock it in the invoking
   shell's existing `ssh-agent` using the established infrastructure procedure.
   Keep its existing public companion available to OpenSSH. No agent is started,
   key generated or password requested by diagnostics.
5. Run `production-check`, review its target and public trust fingerprints, then,
   only after separate operation authorization, run:

```bash
make production-inspect PRODUCTION_INVENTORY=../portfolio-server-infrastructure/inventories/production.yml
```

The inventory path defaults to the sibling path shown above. The CLI displays
address, port, username, alias, existing public trust fingerprints and categories.
It requires an interactive stdin terminal and exact `INSPECT` confirmation.
Enter, EOF, other answers and piped input deny the connection. There is no `--yes`
or environment bypass. One confirmation covers this bounded set of read-only
probes; each uses an independent connection that closes on completion, without
connection sharing or retries. Interrupts kill/reap the local child and stop the
run; an interrupted run does not replace the previous report. No cancellation
can guarantee a remote command has already exited, but all allowed commands are
read-only. Ordinary SSH authentication/audit logs and file access timestamps can
still be produced by read-only access.

## Transport and collected evidence

The transport uses explicit argv with `shell=False`, `-F /dev/null`, strict host
checking, batch mode, the explicit identity and public-key-only authentication.
It disables password/keyboard-interactive/GSSAPI/hostbased fallback, agent/X11/port
forwarding, tunnels, local commands, proxies, connection multiplexing, DNS host-key
trust, automatic key updates and additional known-host commands. Only the
explicit user trust file is consulted; the system file is disabled. The initial
public fingerprints are rechecked before every probe. OpenSSH performs the live
host-key signature verification. The report records the eligible fingerprints,
not a claim about which algorithm/key was negotiated. The only inherited optional
SSH setting is `SSH_AUTH_SOCK` for the selected existing identity.
[OpenSSH option semantics](https://man.openbsd.org/ssh_config) define these controls.

Connect timeout is 8 seconds; each probe has a 20-second total deadline and a
combined 256 KiB stdout/stderr cap. Local trust lookup has a 5-second deadline.
Both pipes are drained concurrently; timeout, cap violation and interruption kill
and reap the process. Raw stderr is classified into fixed diagnostic codes and
never printed or saved. Authentication/host-key/network/transport failures stop
further connections; individual command failures preserve independent results.

Static commands collect only:

- Ubuntu ID/version, CPU architecture/count, RAM KiB, disk availability KiB for
  `/`, `/var`, `/opt`, hostname and machine ID. Other filesystems and a custom
  Docker data root are not exhaustively verified.
- Engine version and daemon ID from the current user's rootful local Docker API,
  supported Compose version, all containers (including stopped), project labels, network
  names/drivers/subnets and volume names/drivers. Each resource includes only the
  manager's managed/application/component labels and the Compose project label.
  Full inspect objects, container environment, mounts, commands, logs, volume
  contents, arbitrary labels and registry credentials are never requested.
- TCP listener ports, Caddy binary version, systemd Caddy/Nginx/Apache/Docker
  load/active state, and unprivileged UFW active/inactive status. UFW rule content
  is not saved. Firewall restrictions/absent tools remain unknown; inactive UFW
  does not prove the host has no other firewall. Listener visibility does not
  prove public internet reachability. Process attribution is not collected;
  another reverse proxy can be detected as an occupied port without identifying
  its implementation. Caddy installed only in Docker may have no host binary.

Docker always uses `--host unix:///var/run/docker.sock`, a minimal environment and
`--config /nonexistent`; no context is changed, remote Engine chosen, credential
helper invoked or rootless socket guessed. Missing socket permissions produce
`docker-permission-denied`, never an assertion that Docker is absent. The account
needs existing read access to that API; **unrestricted Docker group/socket access
is effectively host-root privilege**, even when this tool only issues reads. The
infrastructure owner must approve permissions or a future constrained mechanism;
this layer does not grant access or invoke sudo.

Resources are capped at 128 per kind, with strict response validation. Network IDs
must be exactly 64 hex characters before entering the static inspect loop. Names
and YAML never become remote command text. Projects are visible through container
Compose labels; orphaned project metadata without containers cannot be inferred.
Ownership is only `claimed-managed` or `foreign-or-unknown`: labels never prove
adoption eligibility. Existing reserved names (`portfolio-proxy`,
`portfolio-project-router*`), reserved Compose project, overlapping observed
network subnets, active proxy units and occupied 80/443 or Docker API 2375/2376
ports are blockers requiring review. A proposed proxy subnet is not yet approved;
absence of observed overlap does not allocate one or prove future compatibility.

## Reports and interpretation

For a private C4.1 administrative transcript, the separate offline
[`production-host-evidence` check](production-host-evidence.md#narrow-offline-tool-evidence-check)
extracts only bounded tool observations. It does not rerun inspection, authenticate
the transcript or convert it into host policy/source trust.

Reports use [v1 JSON schema](../schemas/production-inspection-v1.schema.json),
closed probe-specific normalized details, UTC start/finish timestamps, target,
observed identities, trust policy/fingerprints, category/status/reason, conflicts,
unverified prerequisites, blockers and limitations. Status meanings:

| Status | Meaning |
| --- | --- |
| `passed` | The named bounded observation was collected and validated; for OS/architecture/listeners its configured baseline was also satisfied |
| `blocked` | A required capability failed, the Ubuntu 24.04 / x86_64 baseline mismatched, configured SSH listener was absent, or a resource/listener conflict needs review |
| `unknown` | Evidence was unavailable, malformed, denied, incomplete or skipped after transport failure |

`passed` never means the server as a whole is ready for deployment. A valid empty
Docker listing differs from a failed query. Identity values are observations to
compare against the operator's expected server, not an independently attested
machine identity. A snapshot can become stale during the multi-probe run.

Only the latest completed report is retained at
`~/.local/share/portfolio-deployment/production-inspection/latest.json`, outside
the checkout. Newly created directories use 0700; reports use 0600. Unsafe links,
permissions and group/world-writable ancestors are rejected (except system-owned
sticky temporary directories for tests). Existing permissions are never silently
changed. Saving uses an exclusive temporary file, file fsync, atomic replacement
and directory fsync; errors preserve the previous report before replacement.
Reports are capped at 2 MiB. They contain host/resource metadata and should still
be treated as private; do not commit or publish them automatically. Choose any
longer retention or export policy separately.

`production-report` only validates and reads this local file, displaying counts,
blockers and a bounded conflict summary. It never loads inventory or invokes SSH.
Missing, corrupt, oversized, unsafe or unsupported reports fail with a local
error. Exit codes: 0 for a passed local check (or a report without blockers), 2 for
blockers/configuration/report errors, 130 for cancellation. Current reports always
retain Stage 09.3 prerequisites, so a completed inspection normally exits 2;
this does not mean the JSON was not saved.

## Handoff to Stage 09.3 and forbidden actions

The operator must review an authorized fresh report and resolve or explicitly
scope missing Docker permissions, unknown evidence, foreign resources, reserved
names/listeners and subnet conflicts. Still required: approved subnet and resource
budget; deployment/journal/fixed-lock/Caddy paths and ownership; runtime-security
contract; complete fresh production observation/readiness adapters; reviewed Caddy
image/config/LKG and recovery procedures; protected exact-SHA validation and verified
origin-bound digest/platform; separately verified GHCR, DNS, public ports and ACME.
See [production contracts](production-contracts.md#next-stage-prerequisites-and-acceptance).

This layer never invokes a deployment executor or exports a complete Stage 03
ObservedState. Reconciliation admission/freshness/ownership/journal gates are
unchanged. It cannot install packages, run provisioning, use sudo, pull images,
create/restart/delete Docker resources, change contexts/trust/keys, modify DNS,
firewall/SSH/systemd/Caddy/TLS, activate routes or deploy. No mutating retry or
repair path exists. Standard `make check` and CI use synthetic inventories and
fake SSH transports only; implementing or merging this interface authorizes no
real connection. Every live inspection requires separate operation authorization.

## Stage 09.3 integration

Stage 09.3 now provides [offline production integration](production-integration.md).
The inspection package remains independent of deployment/executor imports. Its
report still cannot become complete fresh planner evidence; a passing local
`production-check` is not server readiness. Fresh source ports, exact artifact and
origin evidence, approved identities/paths/subnets and separately authorized
Stage 09.4 operations remain necessary. No real SSH inspection was performed.

## Stage 09.4A compatibility clarification

The shared Compose policy accepts stable plugin versions >= 2 (including future
major/minor/patch versions and Ubuntu build suffixes), with or without `v`. Legacy
v1, prereleases and malformed versions are rejected. Inspection normalization,
Stage 05 prerequisites and official validation use the same policy. A separate
read-only `compose_cli` probe checks required plugin options using the shared
capability policy; unavailable or incomplete evidence prevents readiness. Syntax
of the actual rendered configuration is checked by `config -q` before `up` and
during official validation. Inspection does not upload or validate application
configurations. Version parsing and CLI help are not proof of image/runtime
compatibility.

The operator's 2026-10-22 manual observations reported Engine 29.9.0, Compose 5.6.0,
Docker access only through administrative `sudo -n`, active UFW and an empty host.
The previous 9 passed / 2 blocked / 6 unknown report is not a trusted live snapshot.
`docker-permission-denied` means missing permission, not absent Engine; the missing
host Caddy binary is expected for container-managed Caddy. Codex did not reconnect
to the VDS. No sudo mode was added to inspection. See
[09.4A handoff](production-preparation.md) for full operator evidence and blockers.
