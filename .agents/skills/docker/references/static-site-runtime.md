# Static Website Runtime and Container Validation

Read this reference when Docker-packaging a pre-rendered/SSG or client-rendered static site and serving it from a container behind an independent reverse proxy.

## Build/runtime split

- Install exact dependencies from the committed frontend lockfile in a Node builder.
- Produce the site's static output at build time. Copy only the output into a small static-server runtime (for example unprivileged Nginx or an equivalent non-root HTTP server).
- No Node.js, build sources, dependency tree or package registry credentials are required in the runtime when the output is static.
- Pin sensible base-image versions and evaluate security fixes; pin digests when practical without making updates impossible.
- Build-time public origin settings that affect SEO/SSG output cannot be retroactively corrected by changing runtime environment variables.
- Do not pass secrets through public Vite-style build args; such values can be embedded in public assets and image history.

## HTTP contract

- Verify real HTTP behavior from the running container, not only its file layout.
- Treat root/default locale explicitly: an SSG-generated HTML meta refresh is not necessarily the same as an HTTP 3xx redirect.
- Serve language routes such as `/en/` and `/ru/` directly when generated. Avoid a blanket SPA fallback for SSG sites whose unknown routes should return an HTTP 404.
- Verify paths and MIME types for hashed JS/CSS, images, original PDFs, robots, optional sitemap and other public files.
- The public gateway may terminate HTTPS while an internal static server listens over HTTP.
- Restrict cache headers by asset class: immutable hashed assets differ from HTML, sitemap and robots. Avoid caching broken navigation or stale metadata forever.
- The external gateway must preserve or deliberately configure host/protocol information and must not change static route semantics unintentionally.

## Container contract

- Prefer non-root runtime, minimal capabilities and no writable application files where feasible.
- Production Compose consumes an externally built immutable image reference; do not require a local image build on the VPS.
- Application services normally have no published host ports. Join a pre-existing gateway network with an explicitly agreed service name/alias; the gateway is separately owned.
- For local smoke tests, use a loopback-only port mapping or an ephemeral isolated network. Never require the real production gateway to run local tests.
- The app may be stateless; do not add volumes, migrations or a database just to match generic production templates.
- Distinguish restart policy (exited process) from health status (may be unhealthy but still running). A healthcheck is not an automatic repair controller.

## Validation checklist

1. Build the exact production image, run it and verify the non-root process identity.
2. Request root/default route, each localized route, nonexistent route, JS/CSS, image, PDF and SEO files from the real HTTP server; assert status, content type and expected body.
3. Confirm generated document bytes and all supplied PDF artifacts are present unchanged where preservation is required.
4. Validate the Compose config with required placeholders, its lack of public ports and its external network declaration.
5. Confirm local smoke cleanup removes only test-owned containers and networks; do not touch unrelated Docker resources.
6. Exercise healthcheck, shutdown/restart and startup behavior, with bounded waits and focused diagnostics.
7. Build both with absent public origin and a safe test-only HTTPS origin when origin-dependent SEO output exists.
8. Record what remains unverified until a real gateway/domain deployment (DNS, TLS, redirects, live metadata and public availability).
