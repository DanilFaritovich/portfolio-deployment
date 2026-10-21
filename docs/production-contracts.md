# Stage 09.1 — Production contracts and security

Status: implemented offline; security-review proposal, no production activation.
The real registry remains empty. The admission entry reserves ProjectRouter's
identity and hostname, but cannot authorize a deployment. Stages 09.2–09.5 need
separate scoped tasks and operation approvals. No VDS, SSH, Docker mutations,
DNS, certificates, image publication or other-repository changes occurred in 09.1.

## Audited producer and integration decisions

Read-only GitHub audit on 2026-10-21 used ProjectRouter `develop` revision
[`b375ef86b17e9cb290741e47a30ea898e4d40824`](https://github.com/DanilFaritovich/project-router/commit/b375ef86b17e9cb290741e47a30ea898e4d40824):
[contract](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/docs/deployment/CONTRACT.md),
[proposed manifest](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/docs/deployment/service-manifest.example.yaml),
[Compose](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/compose.yaml),
[Dockerfile](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/Dockerfile),
[Nginx](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/docker/nginx.conf),
[CI](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/.github/workflows/ci.yml) and
[publication](https://github.com/DanilFaritovich/project-router/blob/b375ef86b17e9cb290741e47a30ea898e4d40824/.github/workflows/publish.yml).
The [exact-SHA push run](https://github.com/DanilFaritovich/project-router/actions/runs/39406425221)
completed successfully, including quality, container smoke and publication jobs.
This proves a develop publication, not production origin, digest selection or deployment.

At audit time default `main` was `c849326f507e0319b0c28ac3542e9a894a47231c`,
without these foundation files. GitHub branch metadata reported both `main` and
`develop` as unprotected. Protection/ruleset enforcement must be independently
confirmed later. No GitHub variable or secret values were requested or inspected.
No origin-bound production digest is verified by this audit; this does not assert
that GHCR contains no images.

| Boundary | Producer / previous example | Central contract and resolution |
| --- | --- | --- |
| Manifest | Proposed `schemaVersion/serviceId`, null digest/domain/origin | Translate into existing `portfolio.deployment/v1` Application; never parse producer sample as a manager manifest |
| Network | External `portfolio-proxy` | Manager owns preprovisioning and verifies ownership; application joins it, never creates a competing network |
| Alias | Producer `project-router` | Manager-derived `project-router-web`; manager Compose and Caddy use exactly this alias; no compatibility alias or alias override |
| Resource names | Producer service `project-router`; local project `project-router-local` | Project `portfolio-project-router`, service `web`, private network `portfolio-project-router-private`; no fixed container name; never run producer Compose alongside manager deployment |
| Readiness | Producer `/en/`; synthetic older fixture `/health` | Production template/policy require HTTP `8080/en/`; image has matching wget healthcheck; fixture remains synthetic |
| Identity | GHCR publication reports manifest/index digest | Full `ghcr.io/danilfaritovich/project-router@sha256:` plus 64 lowercase hex characters; no tags, local image ID or bare digest as the manifest image |
| Host/source | Producer leaves domain/origin unset | Only `danil-miftakhov.dev`, source `DanilFaritovich/project-router`, exact GHCR repository and `linux/amd64` admitted |
| State | Stateless Nginx UID/GID 101:101, writable `/tmp` | No persistent volumes, migrations, application secrets or runtime config; origin is public build-time data |
| Ownership prose | Producer contract attributes VPS ownership to controller | Infrastructure repo owns host/Ansible; manager owns deployment/gateway; producer owns image/HTTP/CI. No other repository changed |

The manager is authoritative for its generated resource names. The different
producer alias is a consumer-side translation, not a dependency on changing the
producer repository. No second production Compose file is introduced here.

**Remaining runtime blocker for 09.3:** current manager Compose does not yet
project the producer's `read_only`, bounded `/tmp` tmpfs, capability drop,
`no-new-privileges` and restart policy. Before production execution, implement an
explicit, validated runtime-security contract and renderer support with tests;
verify actual UID/GID 101:101, no writable application filesystem and necessary
writes to `/tmp`. Preserve the image healthcheck. Do not hardcode these settings
for unrelated/stateful applications or bypass the manager by running producer Compose.
Resource limits need measured host/workload sizing. This stage does not claim
production-ready runtime packaging or add production transport code.

## Inactive registration and admission enforcement

[policy/admission.yaml](../policy/admission.yaml) is a narrowly scoped security
review proposal. [Template](templates/project-router.yaml) lives outside
`registry/applications` and remains `enabled: false`. `${PROJECT_ROUTER_DIGEST}`
is intentionally rejected by the ordinary validator. There is no production
manifest containing a dummy hash, test hostname or nullable image identity.

To prepare a later registration, obtain the artifact evidence below, substitute
the entire digest token with its verified `sha256:<64 lowercase hex>` value and
review the candidate through a PR. It must remain outside the active registry
until evidence is accepted; the eventual registered declaration remains disabled
until a separately reviewed activation change. Copying/enabling a declaration,
merging it or rendering Compose never grants operation authorization.

Optional admission `staticSite` pins one HTTP component, port, readiness path and
expected HTTPS build origin. It requires matching ingress, stateless storage and
no config/secret references, dependencies or mounts. Policy origin must match its
sole admitted hostname and component image-map key. Existing policies without
`staticSite` retain their previous checks; disabled apps receive every check.
Global duplicate hostname/alias checks still apply. There is no mutable-image
exception or admission bypass.

`buildOrigin` constrains the expected origin and ingress only. The offline
validator cannot inspect SSG bytes, prove image provenance, distinguish a Docker
image ID from a registry digest with identical syntax, verify GitHub protections
or authorize a human. Those are separate fail-closed production gates.

## Production release and security policy

The first production path is **manual deployment from protected `main`** in both
producer and manager repositories. Develop artifacts and arbitrary `v*` tags are
ineligible. A future release-tag source needs a separate policy review including
protected tag creation and ancestry. No dispatch, polling, webhook, push-triggered
apply or auto-activation is introduced. Ordinary merge and GHCR push only make
candidates available. There is still no production apply target/interface.

Before first deployment, the operator/reviewer must assemble an approval record
binding all of the following; absence or mismatch blocks execution:

1. Reviewed protected manager `main` full SHA, exact manifest/policy revision,
   successful trusted validation of that exact SHA and inspected plan/candidate
   hashes. Existing PR-only `Offline checks` can validate a merge ref; they do not
   by themselves prove the final production SHA. A later scoped task must provide
   trusted exact-SHA validation before apply, without adding automatic activation.
2. Producer protected `main` full SHA with successful `Quality and static build`
   and `Docker build and container smoke` for that SHA, followed by the same-SHA
   publish job. Current `needs: [quality, docker]` ordering is compatible. A green
   unrelated SHA, PR merge ref or canceled/pending run is insufficient.
3. Exact GHCR repository/digest, successful registry retrieval, retained OCI
   manifest/index bytes and SHA verification, source/revision provenance matched
   to the trusted publish run. OCI labels alone are self-assertions. If using an
   index, record verified `linux/amd64` child manifest/config/runtime image mapping;
   never equate an image ID or guessed tag resolution with selected artifact identity.
4. Evidence that this digest was built with
   `VITE_SITE_ORIGIN=https://danil-miftakhov.dev`, including publish build inputs
   and origin-bearing output (canonical/hreflang, sitemap/robots, JSON-LD and
   OG/Twitter metadata). Current minimal provenance may not establish build args;
   origin uncertainty requires new producer-side evidence/build in a separate task.
   Runtime env cannot repair an SSG build. Preserve old known-good digest availability.
5. Identified host/daemon and SSH host-key fingerprint, fresh complete trusted
   observations, readiness/rollback plan, operator/window and explicit first
   deployment authorization for the exact revisions, digest and hostname. Record
   outcome and bounded failures in the existing journal; reapproval is required if
   inputs change. No approval is inferred from this document or PR merge.

GitHub administrators must protect `main` and `develop` against direct/unreviewed
changes and require the actual CI check names. Review manifest, admission,
workflow and executor changes with a security-aware reviewer; an author cannot
self-approve the production policy. If GitHub plan limitations prevent independent
Environment review, use a recorded out-of-band operator approval and keep execution
manual; never claim the control is configured merely because it is documented.
A future manual workflow must restrict source to protected `main`, use a protected
`production` Environment, bind exact SHA/digest/plan approval, minimize token
permissions and serialize deployments. PR CI receives no production credentials;
no `pull_request_target` execution of untrusted code with privileged tokens.

Infrastructure Ansible remains the only owner of SSH users, host hardening,
Docker installation and firewall. Consume its reviewed inventory/interface and
operator handoff; do not rerun or modify it from the manager. A dedicated non-root
deployment user should have pinned host trust and a reviewed command/path allowlist.
Unrestricted Docker socket/group access is effectively host-root access: document
and approve that trust, or implement a constrained mechanism before use. Avoid
unrestricted sudo, shell commands derived from manifests and agent forwarding.
Never mount the Docker socket into ProjectRouter or Caddy. Read-only diagnostics
also require separate permission and bounded/redacted output.

Git contains symbolic config/secret references only. ProjectRouter needs no runtime
injection; `VITE_SITE_ORIGIN` is public build input, not a GitHub Secret. Provision
future secrets server-side under infrastructure policy, outside Git/journal/logs,
with restrictive permissions, rotation and redaction. Prefer anonymous verified
GHCR reads if public; if private, arrange a separately authorized least-privilege
read identity later. No credentials or secret values are requested by 09.1.

Preserve all foreign containers, projects, networks, volumes and gateway routes.
Names alone never establish ownership: require manager/application/component and
Compose labels plus expected topology. Unknown/incomplete/foreign ownership or
unrelated listeners blocks the affected operation; never adopt, rename, prune,
`down`, unlink the fixed lock, or delete persistent data to resolve a collision.
Preexisting `portfolio-proxy` must be reviewed before use. Host flock, fresh
observations, intent-before-mutation, retained LKG and recovery fences remain
mandatory. Unknown mutation outcome requires diagnosis, not automatic replay.

Only Caddy intentionally publishes 80/443; no application host port. Only necessary
HTTP components join the shared bridge; it provides reachability, not isolation
between members. Caddy terminates publicly trusted TLS for the exact approved
hostname; no wildcard, catch-all, `www` or alternate domain is approved. DNS
ownership/IPv4/IPv6 and ACME reachability must be demonstrated before issuance.
Persist/protect Caddy data/config and its LKG; keep admin API inside the management
boundary, with no Docker socket. Validate the full candidate, probe retained routes,
activate only after internal readiness, then verify TLS and affected/retained routes.
No reload for image-only updates. Retain the previous working config on failure.
Do not disable certificate verification or use internal/test TLS in production.
HSTS is deferred until HTTPS/redirect policy is reviewed; forwarded client identity
must follow an explicit trusted-proxy chain, never trust arbitrary client headers
or the whole shared bridge. Respect Ansible firewall policy and Docker port behavior.

## Next-stage prerequisites and acceptance

Every row requires its own scoped authorization; completion of 09.1 permits none
of the listed live actions automatically.

| Part | Required inputs / permission | Acceptance evidence |
| --- | --- | --- |
| 09.2 — VDS read-only diagnostics | Explicit read-only SSH authorization, endpoint/port/user, independently confirmed host-key fingerprint, reviewed Ansible revision/handoff and allowed commands | Bounded redacted report: OS/architecture, Engine/Compose/Caddy versions, daemon identity, disk/RAM, listeners/firewall/Docker exposure, networks/subnets/labels, foreign projects, paths/ownership and journal/lock location. No installs/pulls/reloads/mutations. Missing/unknown scopes are blockers, never fabricated complete Stage 03 state |
| 09.3 — Production adapters and Caddy | Accepted diagnostics, approved paths/network/ownership model, trusted exact-SHA validation path, pinned Caddy image/version, runtime-security and secret-reference contracts; separate permission for any host changes | Production transports reuse executor/journal/readiness, bound all reads/commands and resolve verified OCI mapping; fail closed on incomplete sources. Tests cover no-op, foreign preservation, image-only no-reload, retained routes, rejected config, timeouts/partial failures/crash fences. Reviewed Caddy HTTPS candidate/LKG and recovery procedure. Offline tests first; live checks only in explicit scope |
| 09.4 — First ProjectRouter deployment | Approved protected-source revisions/CI, verified origin-bound digest and pull access, reviewed enabled manifest, DNS/ACME prerequisites, accepted adapters and explicit mutation/activation window | Internal `8080/en/` healthy before route activation; valid public TLS for `danil-miftakhov.dev`, `/` 302 to `/en/`, EN/RU documents, unknown route 404, assets/PDF/MIME and origin metadata verified. No app host ports, unrelated resources/routes preserved, readiness/journal/LKG recorded; failure leaves truthful partial state. First deploy has no prior app LKG: stop and diagnose rather than invent rollback |
| 09.5 — Update, rollback, production acceptance and release | Second approved digest, retained known-good artifact/config, rollback authorization, recovery/backup/retention owners, monitoring and acceptance window; separate release approval | Demonstrate unchanged no-op, update without gateway restart, unhealthy-update preservation/rollback, operator recovery and public recheck. Stateless image rollback does not restore host/Caddy state. Report downtime and production evidence separately from VM tests; approve production acceptance before release PR/tag. License decision, release revision/CI, operator runbook and separately authorized main/tag/release required |

Later the operator must provide **non-secret facts and references**, not paste
credentials into chat/Git: VDS hostname/IP, SSH port/user and trusted fingerprint;
infrastructure revision and provisioning/backup owner; intended Docker endpoint,
resource budget and approved deployment/journal/Caddy paths; inventory of existing
resources/gateway and reserved networks; DNS owner plus A/AAAA/CAA state and public
80/443 reachability; ACME account/contact policy and protected certificate storage;
GitHub branch/ruleset/Environment approval configuration and exact check identities;
producer origin configuration evidence, publish-run URL/SHA and selected digest,
platform mapping and GHCR visibility/retention; runtime-config delivery references
(if later needed), maintenance window, independent monitoring and recovery owners.
Do not request SSH private keys, tokens, secret values or TLS private material.

Ready for 09.2 means contracts, inactive template, narrow admission, offline tests
and a defined diagnostics scope are available. Host access, DNS/TLS, trusted release
artifacts, protected-source controls and production acceptance remain unverified.
