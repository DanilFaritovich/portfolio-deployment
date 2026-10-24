# Fixed production bindings and public postconditions

Implementation and offline verification do not authorize installation or rollout.
`installation/bindings.py` selects `fixed_bindings.build_fixed`; its sealed owner
policy is deliberately `None`. A separately reviewed replacement binding must
construct `FixedPolicy` in trusted Python before the production bundle is built.
Operation JSON, environment variables and phase-review documents cannot select
these dependencies. No production policy values or approvals are supplied here.

## Concrete composition

The fixed recipe uses the existing EngineContract at `unix:///run/docker.sock`,
AdministrativePhaseReview/HostPolicy, EmptyHostReader, BootstrapResources,
CaddyReleaseCollector, pinned gh/Sigstore AttestationVerifier and WorkflowCheck.
GitHub uses the fixed root-owned 0600 `/etc/portfolio-deployment/github-read-token`;
Caddy GHCR credentials reach only the existing fixed token endpoint. ProjectRouter
uses the existing anonymous GHCR bearer-token transport and OCI/provenance gates;
its image preparation factory remains `project_router_image_registry`.
The fixed CA PEM also supplies GitHub/GHCR HTTPS trust, without ambient CA directories.
Existing transport request/deadline bounds remain enforced.
`FixedPolicy.producer_checks` is the complete independently reviewed tuple of
ProjectRouter required workflow checks. Both phase recipes preserve every entry
and append the manager Offline check. Empty, duplicate or foreign-repository
entries fail before installation I/O; ProtectedSource still requires equality
with the effective mandatory check set rather than a selected subset.

The empty phase needs no invented runtime IDs. Rollout obtains IDs and complete
infrastructure membership only through `build_reviewed_phase` and the successful
bootstrap journal, then rechecks live PinnedCaddyRuntime/UDS/ACTIVE/SourceTrust.
SSHSIGVerifier still authenticates separate operation approvals. The administrative
binding now selects `authenticated_file_decision`; absence of its independently
installed standalone helper or independently published exact one-use decision
fails closed. Neither recipe issues an approval or adds an execution engine.

Fixed owner inputs still missing include machine/daemon identity, reviewed IPAM
and budget, public address/resolver/CA policy, exact image/source/check/builder
selections, gh and Sigstore-root pins, scoped read credential and the external
observer public key. The phase authority separately supplies actual HostPolicy,
recovery/custody evidence, Caddy runtime acceptance and later observed IDs.
Operator public trust and independently reviewed external host inventory remain
mandatory. Do not derive these approvals from a checker-generated report.

## Public DNS/TLS/HTTPS port

`PublicPostconditions.verify(routes, identity)` preserves GatewaySource's existing
return contract. It admits exactly the ProjectRouter web route, port 8080 upstream,
with its exact manager revision, for `https://danil-miftakhov.dev/en/`. Public TLS
always uses port 443. A and AAAA are queried separately through an approved numeric
resolver; both complete answer sets must equal their explicit approved sets.
An empty family explicitly forbids its DNS records. At least one and at most four
public addresses are admitted. CNAME, truncated/duplicate/wrong-type answers,
NXDOMAIN and unknown/mixed addresses fail closed; there is no resolver fallback.
This resolver policy is an input, not a claim of DNSSEC validation.

Each approved address receives a direct numeric connection with fixed Host/SNI,
peer comparison and TLS chain/hostname validation against only the hash-pinned
PEM. No proxy, URL input, TLS bypass or redirect is available. The request uses
identity encoding and Connection: close. Acceptance requires HTTP 200, HTML,
exact Content-Length and SHA256 of independently reviewed `/en/` response bytes
for the selected ProjectRouter artifact. Chunked/compressed/redirect responses
fail closed. The limits are two DNS queries, four HTTPS requests, 2-second socket
steps, 15-second overall verification, 16 KiB headers and 256 KiB body per response.
DNS/network, TLS, HTTP, route, identity, CA and witness failures have fixed codes.
The constructor performs no network I/O. Controlled transports exercise failures
offline; actual production DNS/TLS/HTTPS has not been checked by these tests.

## Independent external availability

A VDS-local request cannot prove Internet reachability. Full verification also
requires `SignedPublicWitness`: a fresh independently signed off-VDS observation
at `/var/lib/portfolio-deployment-public/observation.json`. This mutable evidence
is outside executable pins. Protected ancestry and regular-file checks still
apply; file ownership alone is insufficient, because the signature is mandatory.

Before rollout, independently approve an off-VDS observer, its public key, clock
and delivery policy. Its private key must remain off VDS. On that observer, run
`PublicPostconditions(policy, external=None).probe(routes, identity)` from trusted,
reviewed tooling using the same sealed public policy and expected routes. Only a
successful real probe returns the subject. Sign canonical UTF-8 `encode(payload)`
bytes, where payload has exactly `subject`, integer `observed_at`, and integer
`expires`, with `observed_at < expires <= observed_at + 30`. Use an existing
approved Ed25519 observation key and `ssh-keygen -Y sign` with namespace
`portfolio-public-https-v1`; no key is generated by the manager.

Publish the bounded canonical envelope `{"payload": payload, "signature": armor}`
through a separately approved authenticated evidence channel into that fixed
protected file. The signature principal is `public-observer`. The verifier binds
the exact origin/path, complete routes/revision, machine/daemon identity and public
policy digest, rejects future/expired/>30-second-old evidence and verifies with
the sealed observation public key and pinned `/usr/bin/ssh-keygen`. An operation
signature has a different namespace and cannot substitute. The receipt may serve
multiple stable observation rounds only within that short window and exact subject.
The owner must arrange timely off-VDS execution/delivery during the rollout window;
no observer host, key or delivery channel is configured here. Missing evidence
blocks promotion. No monitoring service or remote command runner is introduced.

For the first route, arm the separately authorized observer before starting the
rollout. It probes until the real route succeeds, then signs and atomically
delivers the receipt. The consumer waits for absent or stale/unbound evidence
only within the remainder of the existing 15-second public verification budget;
invalid signatures or unsafe metadata fail immediately. Keep observation, signing
and delivery within the 30-second evidence lifetime, and delivery within that
remaining local budget. Continue fresh observations through all stable rounds.
Offline integration exercises activation followed by real SSHSIG signing,
delayed atomic file delivery and two verifications using real clocks. It does
not establish production DNS/TLS, ACME timing or channel latency.

## External closure and protected-main release

Keep package-artifact verification, historical host observations and actual host
closure separate. Retain previously authenticated package/wheel evidence; do not
repeat passed hashes. Additional public-probe imports use the existing stdlib,
OpenSSL/socket and OpenSSH closure, without another wheel or tool. A local isolated
Python 3.12 import and static wheel ABI review do not establish VDS provenance.

Only the remaining installed package selections and selected resolved regular-file
hashes/metadata are needed: Python stdlib/extensions, native libraries/interpreter,
actual loader cache/config/search paths/aliases, dlopen/NSS/OpenSSL provider and
configuration inputs, approved CA PEM and Sigstore roots, final standalone helper/
admin binding and operator public trust. Resolve exact paths against independently
authenticated candidate artifacts after installed revisions are known. A package
CA directory does not prove a generated host CA bundle. Do not use ldd, install
packages, rescan the whole host or open another SSH connection without permission.
The exact remaining candidate/path analysis is private, outside Git and CI artifacts.

The next release sequence is:

1. Review this feature PR into develop, including the real adapters and both recipes.
   Independently approve the final replacement binding bytes and remaining external
   inputs. Finish actual host closure and independently confirm the SSH host key
   through the provider. Do not checksum a partial inventory as an approval.
2. Separately authorize the reviewed develop-to-protected-main PR. Verify effective
   policy B and successful exact-main-SHA push `Offline checks`, App 15368, workflow
   `.github/workflows/offline.yml`. A feature/PR check is insufficient. Do not merge,
   bypass protection or create a release as a side effect of this task.
3. Select that exact manager SHA. Use the existing deterministic builder twice with
   identical reviewed vendor/Caddy assets, dependency pins and final replacement
   binding, writing two absent output paths. Compare complete ZIP bytes with `cmp`.
   The frozen vendor/Caddy locks are unchanged by this implementation.
4. An independent reviewer uses trusted tooling/pins, not candidate-bundle policy:
   instantiate `ManagerBundleVerifier(github=GitHubRead(token, HTTPSRead()),
   checks=(WorkflowCheck('DanilFaritovich/portfolio-deployment', 'Offline checks',
   15368, '.github/workflows/offline.yml'),), asset_pins=reviewed_asset_pairs,
   bindings_pin=reviewed_binding_sha256)`. Call `.verify(bundle_bytes)` and compare
   `manager_sha` and `bundle_sha256` with independently approved exact values.
   Existing whole-tree/source-policy-before-and-after checks and deterministic
   reconstruction remain mandatory. See the [installation runbook](first-production-installation-runbook.md)
   for the full command interface. Runtime modules must also import under isolated
   Python 3.12 using only the exported bundle/vendor, without the checkout/dev env.
5. Only then obtain a fresh <=1-hour one-use exact installation decision, complete
   sorted external inventory and separate permission to publish/install the exact
   helper/admin binding/bundle. Installation does not authorize start, acquisition,
   bootstrap or rollout. Those existing gates remain separate.

A reproducible feature-branch ZIP can validate packaging, but is not a trusted
production bundle. Protected-main source, reviewed final bindings, complete actual
external closure and independent whole-bundle verification are still required.
