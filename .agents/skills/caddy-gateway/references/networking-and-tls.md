# Shared Docker Networking and HTTPS

Read when changing the public network boundary, TLS policy, DNS assumptions, proxy headers or external gateway verification.

## Network boundaries

- Create/manage the external proxy network in the central deployment repository; individual applications join the pre-existing network by declared name.
- Caddy reaches upstreams by stable Docker DNS alias and **container** port. Container port exposure (`expose`) is not the same as publishing a host port (`ports`).
- Avoid publishing individual application ports to `0.0.0.0`. For local tests prefer loopback-only ephemeral mappings and isolated test network names.
- The shared proxy network is **not** a security boundary between its members: containers on it may reach exposed services directly. Only attach necessary frontend/API gateways; databases/cache should remain on separate private networks.
- Docker published ports may bypass ordinary host-firewall assumptions. Verify actual host listeners and firewall/packet-filter behavior rather than trusting a UFW rule alone.

## Caddy runtime

- Publish 80/443 only from the public gateway, subject to operator-approved IPv4/IPv6 and firewall policies.
- Persist Caddy's data and config state so certificate material and operational state survive container replacement. Protect volumes and backup access.
- Prefer an explicitly pinned, supported Caddy image and safe non-root/least-privilege settings compatible with binding/publication.
- Do not mount unrestricted Docker daemon sockets. If future dynamic discovery needs one, redesign around a narrow authenticated controller interface.
- Avoid leaking internal admin endpoints outside the controlled management boundary.

## HTTPS prerequisites

- The operator must own/authorize domains and configure DNS to the public IP; public reachability and ACME challenge behavior depend on the actual network.
- Production issuance can fail because of DNS propagation, blocked ports, rate limits or CA outages. Document failure and recovery paths; do not silently disable TLS.
- HSTS, redirects, content-security policies and external origins must be reviewed for real deployment. Incorrect HSTS can lock users into broken HTTPS.
- The application build may embed canonical/sitemap/OG origin; deployment must check that image origin and configured host agree.

## Proxies and monitoring

- Decide the trusted-forwarded-client-IP chain explicitly. Do not treat all `X-Forwarded-For` input as trustworthy.
- Gateway health, TLS readiness, upstream readiness and end-user route success are different signals. Test them separately.
- A gateway outage affects all publicly routed apps. Run independent monitoring and document console/SSH-based recovery.
- Container restart policies help when processes exit; they do not automatically heal a running but unhealthy container.
