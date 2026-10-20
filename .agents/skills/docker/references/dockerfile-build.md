# Dockerfile Build Layers and Dependency Caching

Read when creating or changing Dockerfiles, dependency installation, build context, build caching, or multi-stage packaging. Keep the mandatory decisions in the parent `SKILL.md`; this file gives implementation guidance.

## Layer invalidation

- Docker reuses a build layer only when its inputs and relevant preceding layers remain cache-compatible. A changed `COPY` input invalidates that instruction and dependent later steps.
- Copy the *minimum complete dependency metadata* before installing dependencies, and copy frequently changing application source afterward. Do not `COPY . .` before a dependency-install step by default.
- Include all files actually needed by the dependency resolver or packaging step. A `pyproject.toml` that refers to local README files or workspace members may require an adapted two-phase installation; do not omit required files just to improve caching.
- Use a project-appropriate, committed lockfile and a frozen/locked install mode where supported. Prefer stable, supported base-image versions. Pinned dependency inputs improve reproducibility; layer ordering alone does not.
- Keep `.dockerignore` narrow enough to exclude irrelevant and sensitive files (including `.env`, `.git`, local virtualenvs, `node_modules`, caches, and build outputs) without excluding manifests, lockfiles, or needed source.
- Combine operations only when they belong to one invalidation boundary; do not collapse unrelated build steps into an opaque `RUN` merely to reduce layer count.

## Python

For a requirements-based project, the typical ordering is:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
USER 10001:10001
```

Adapt paths, entrypoint, runtime file ownership, and installed package strategy to the actual application. A change inside `app/` should not force `pip install` to run again; a change to `requirements.txt` should.

For `uv` projects:

- Copy `pyproject.toml` and `uv.lock` plus any workspace/package metadata needed to resolve dependencies.
- Install third-party dependencies separately from local project code where feasible (for example, `uv sync --locked --no-dev --no-install-project`), then copy the project and complete installation in locked mode.
- Ensure the final runtime actually contains the intended environment and application; avoid accidentally installing development-only dependencies or depending on a local workstation virtualenv.

For `pip`, prefer a pinned requirements/constraints set where reproducibility matters, rather than relying on an unconstrained resolution at every build.

## Vue / Node

- Copy `package.json` and the chosen lockfile before `npm ci` or `pnpm install --frozen-lockfile`.
- Install dev/build dependencies in the *builder* stage when needed; do not omit them prematurely with production-only install flags.
- Copy application source after installing dependencies. Build assets in a dedicated stage and copy compiled output into a lightweight static runtime image.
- Do not ship `node_modules`, build tools, or the whole development tree in the final static-serving image unless the runtime demonstrably needs them.

## BuildKit cache mounts

- When BuildKit is available and repeated dependency installation is expensive, consider a cache mount for package downloads (for example, `RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt`).
- A cache mount speeds up a *re-executed* install step; it does not prevent layer invalidation. Cache directories must match the package manager and build user.
- `pip --no-cache-dir` disables pip's download cache and therefore defeats a pip cache mount; choose one strategy intentionally. Without a BuildKit cache mount, `--no-cache-dir` can help keep the image smaller.
- Reuse the appropriate package-manager cache for uv, npm, or pnpm only if the build environment supports it. Treat external/remote caches as untrusted acceleration, not a replacement for lockfile verification.
- Never persist credentials into image layers or general-purpose cache mounts. For build-time secrets, use dedicated BuildKit secret mounts when supported; do not pass secrets via `ARG`/`ENV` or `COPY`.

## Multi-stage and final image

- Separate compilers, dependency installers, and frontend build tools from the runtime when doing so reduces size or attack surface.
- Copy only required artifacts and runtime dependencies to the final stage. Avoid copying test data, credentials, local caches, or package-manager state.
- When copying files for a non-root runtime, arrange correct ownership and permissions (for example, using `COPY --chown` where needed); do not fall back to root to solve writable-directory problems.
- Keep OS package installation scoped, with package indexes cleaned as appropriate. Do not install extra tools into production solely to make a health check easy.

## Targeted validation

1. Validate the build for the changed service and ensure its final stage starts correctly under the intended runtime user.
2. Where useful, build twice and check that unchanged dependency steps are reused; make a source-only edit and confirm dependency installation is still cached.
3. Make a lockfile/dependency edit and confirm required installation steps rerun.
4. Validate the Compose configuration and required application health behavior separately; a cached build is not evidence of a healthy service.
5. Keep successful build output concise; expand only failing steps, as described in `references/validation-output.md`.
