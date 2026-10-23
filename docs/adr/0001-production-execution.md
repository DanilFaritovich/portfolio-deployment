# ADR 0001 — Local production execution with a separate privileged boundary

Date: 2026-10-22. Status: accepted preparation design; execution not provisioned.
Scope: Stage 09.4A. First production operations require Stage 09.4B approval.

## Context

The managed inventory user `ansible` has no Docker socket access. The operator
reports `sudo -n docker info` works. These are different permissions: neither SSH
access nor a successful administrative read authorizes application mutations.
Docker administration is root-equivalent. A new Unix UID alone cannot confine it.
No daemon, broad sudo wrapper or new deployment engine is justified for one VPS.

## Options

| Option | Advantages | Risks / prerequisites |
| --- | --- | --- |
| One-shot controller/executor on VDS, separate approved service execution identity | Existing flock, SQLite and executor share a host/process; internal IP probes work; fewer remote unknown outcomes; future CD can submit bounded approved candidates | Requires infrastructure-owned installation/access policy, immutable trusted code and explicit root-equivalent execution review |
| Workstation controller with pinned SSH and privileged remote mechanism | Reuses inventory and existing SSH trust; convenient operator previews | Still needs a privileged remote component; workstation flock does not serialize the daemon; remote disconnects complicate journal/lock ownership; local SQLite cannot prove remote operation outcomes |

## Stage 09.4B.4 ownership clarification

[Operator installation](../production-installation.md) supersedes references below
to Infrastructure installing/owning the manager unit and storage. Server Infrastructure
prepares the host only. Deployment supplies its own independently authenticated
administrative initial bootstrap, because the uninstalled executor cannot authorize
itself. This keeps the host-local one-shot root-equivalent decision and its trust
boundaries; it does not delegate portfolio runtime lifecycle to infrastructure or
introduce a remote shell. The channel and its trusted bindings remain unconfigured.

## Decision

Recommend the first option. Inspection and planning remain unprivileged and
read-only. Mutation will run only as an independently approved, root-owned,
one-shot service identity (`portfolio-deployment-executor` is a proposed service
name, not an installed account/unit). Since the existing rootful Docker socket
cannot be reached by the managed user, a narrowly installed privileged execution
mechanism is required. A root-owned one-shot unit running the approved manager
release as UID 0 is one infrastructure-review candidate. Its host-equivalent
power must be explicit; do not present it as a sandbox or as a dedicated non-root
UID with magically limited Docker rights. Any less privileged broker design must
receive a separate review before implementation.

The same existing `execute`, lifecycle and journal must own the entire approved
transaction inside that process, including the canonical host flock. An
unprivileged planner may submit data; it cannot choose executable paths, sudo
arguments, Docker endpoints, environment, scripts or arbitrary commands.
Infrastructure installs the exact manager revision, pinned dependencies and
launcher in root-owned, non-writable locations. No writable checkout, user
`PYTHONPATH`, environment hooks or manifest command/entrypoint fields become root
code. Authentication and candidate verification precede all writes.

No root shell, `sudo docker ...` relay or unrestricted sudo wrapper is an
acceptable temporary implementation. The current `UnconfiguredPrivilegedExecution`
always blocks. No service installation, sudoers, group/socket permission change,
production authorization issuer or production apply is part of this change.

## Trust and future CD

Inventory and pinned SSH trust identify the host for separately authorized
inspection/installation. They never grant execution rights. Approved machine ID
and daemon ID must independently match fresh Engine evidence. Runtime, gateway
and readiness sources each require expected identities and a trusted attestor
bound to their exact payload and sampling round. Saved reports cannot mint trust.
Trusted Python adapters are part of the installed controller's trusted code;
the interfaces do not defend against malicious code already running inside it.

Future CD may propose a digest-update PR, then request a narrow signed candidate.
It must not run arbitrary GitHub checkout code as root, acquire host Docker
credentials in untrusted PRs or bypass the same lock/authorization boundary.
Stage 09.6 remains a separate task after manual rollout/update/recovery acceptance.

## Infrastructure handoff (blocked until separately approved)

The infrastructure owner must review and provision an immutable one-shot
execution mechanism, exact permitted invocation and authenticated approval input;
choose the service/account and root-equivalent authority; provision protected
canonical lock/storage; pin manager/dependencies; and establish operator console,
backup and failure handling. Keep `ansible` outside Docker groups and keep socket
permissions unchanged. Do not extend existing administrative sudo permission by
inference. No changes to that repository were made here.

A workstation transport remains a possible later alternative only if the entire
transaction and host lock stay inside the approved remote executor. Reusing
Stage 09.2's fixed read probes is not a mutation transport.
