---
name: caddy-gateway
description: Build and verify a shared Caddy HTTPS gateway for multiple independently deployed Docker applications. Use when defining domains, reverse-proxy routes, Caddy configuration, certificates, network integration or safe gateway reloads.
---

# Shared Caddy Gateway

Use this skill when a central deployment platform owns the public reverse proxy for independently maintained applications. For general Docker packaging or GHCR publishing use the existing `docker` and `continuous-delivery` skills; for service registry and rollouts use `gitops-deployment`.

## Ownership

- The gateway repository/controller owns Caddy runtime, public host ports, route generation, TLS settings and persistent certificate storage.
- Application repositories own their images, internal HTTP contract and build/test/publish pipelines. Adding an application does not move its source or CI into the gateway repository.
- Caddy is the HTTP(S) gateway, **not** the application deployment engine. Gateway registration is coordinated by the deployment controller, not an implicit Caddy feature.
- Keep the gateway independently deployable and operational when one upstream application fails. An application image update should not recreate or restart Caddy.

## Public entry point and topology

- Make the gateway the only intentional public HTTP(S) listener (normally 80/443); application and database containers do not publish public host ports.
- Use a deliberately provisioned shared Docker proxy network. Attach only services that must be reachable by the gateway; keep databases and other private dependencies off it.
- Route to stable service names/aliases and internal ports defined by explicit application contracts. Do not infer the upstream port or assume arbitrary repositories are deployable.
- Preserve host-specific hardening from the separate server-infrastructure project; never bypass firewall or Docker port controls for convenience.
- Avoid mounting a Docker socket into Caddy or giving the proxy Docker administrative privileges.

## Domains and certificates

- Admit only explicit, validated, unique domain/route ownership from an approved service registry. Reject unsafe characters, overlapping hosts, ambiguous path precedence and accidental wildcard/catch-all exposure.
- Obtain and maintain TLS certificates through Caddy's supported automation where appropriate; persist its data/config state separately from ephemeral container state.
- Real DNS, externally reachable 80/443 and certificate issuance depend on the operator and environment; an offline Caddy validation does **not** prove public HTTPS works.
- Do not use permissive TLS settings, disable certificate checks or substitute a made-up production domain to satisfy tests.

## Generated configuration and updates

- Prefer a deterministic source-of-truth registry -> canonical Caddy configuration; do not maintain competing manually edited and generated routing rules.
- A valid service declaration must identify its domain, upstream service/port and any deliberate path routing. Reject missing, conflicting or untrusted inputs before changing the gateway.
- Before applying a route change, render to a temporary candidate, inspect the diff, validate the complete candidate with an appropriate Caddy command/adapter and preserve the previous known-good config.
- Apply a validated change with supported hot reload where possible; avoid full container recreation. Fail closed and keep/restore the last working configuration when validation/application fails.
- Test the affected host/route after reload using bounded checks. Distinguish an upstream outage from a Caddy/configuration failure.
- Do not change gateway routes when only an application digest changes and routing contract stays identical.

## Security and observability

- Keep Caddy runtime isolated with minimal privileges and persist only needed data/config volumes.
- Log configuration and routing failures without exposing tokens, secrets or full sensitive headers.
- Decide explicitly whether forwarded headers and client IP information are trusted; do not blindly trust every container on a shared Docker network.
- Plan a separate health/availability check for the gateway. Docker health status alone does not restart an unhealthy-but-running process.

## Workflow and evidence

1. Identify service ownership, required domain/route and upstream contract. Separate offline development from live production activation.
2. Make the smallest gateway/config-generator change with deterministic fixture-based tests.
3. Validate generated routes, duplicate-domain rejection, invalid upstreams and config syntax without live DNS.
4. Test a local gateway against mock upstream containers, including reload and preservation of unrelated routes.
5. Run the project's relevant Makefile checks and offline CI once after the implementation stabilizes.
6. Report exact verified behavior, pending DNS/TLS prerequisites and any live recovery limitations.

When implementing a generator, candidate validation or safe reload, read [references/config-generation-and-reload.md](./references/config-generation-and-reload.md).
When designing shared-network exposure, public HTTPS, forwarded headers or external smoke tests, read [references/networking-and-tls.md](./references/networking-and-tls.md).

**Never perform real DNS changes, open production ports, change production gateway routes, or apply VPS changes without the user's authorization for that operation or a previously approved deployment policy.** An approved PR or green CI is not itself authorization for first production activation.
