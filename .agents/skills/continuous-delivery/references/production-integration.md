# Continuous Delivery Production Integration

Read this reference after the repository CD foundation exists and the real VDS/GitHub production prerequisites are available.

The goal is to connect the prepared repository artifacts to the production environment and verify the complete deployment path.

## Preconditions

Before changing integration logic, confirm the foundation artifacts exist and identify the documented contract.

Expected external prerequisites normally include:

- reachable VDS;
- Docker + Compose installed;
- deployment user;
- stable deployment directory;
- SSH key-based access;
- host-key verification data;
- GitHub `production` Environment;
- required production secrets/variables;
- DNS/reverse-proxy prerequisites when applicable.

Do not silently substitute invented values when a required prerequisite is missing.

If a missing external prerequisite prevents safe verification, report it precisely rather than redesigning the whole foundation.

## GitHub Environment

Use a protected GitHub Environment, normally:

```text
production
```

Keep deployment credentials and production runtime secrets scoped to that Environment.

Non-sensitive values may use Environment Variables.

Validate required Environment inputs before network mutation. Each validation step must explicitly receive the variables/secrets it checks; do not reference an undeclared shell variable and mistake that failure for a missing production configuration.

Do not expose production Environment secrets to untrusted Pull Request jobs.

Use only the permissions the deployment job requires.

## SSH transport

For a single VDS, SSH is an acceptable deployment transport.

Use:

- key-based authentication;
- the documented deployment user;
- host-key verification;
- a stable server path such as `/opt/<project>/`.

Do not disable host verification for convenience.

If the deployment user talks directly to the Docker daemon through the host socket/group, document that this usually grants host-equivalent control. "Non-root SSH user" alone is not a strong Docker privilege boundary.

Do not use root SSH when the deployment user has sufficient scoped permissions.

## Runtime files

Synchronize repository-controlled deployment files to the stable server directory during deployment.

Typical server state:

```text
/opt/<project>/
├── compose.production.yml
└── .env
```

The Compose file comes from the repository revision being deployed.

The runtime `.env` comes from the protected GitHub Environment.

Never print the production env contents into workflow logs.

When writing runtime/deployment files:

- create replacements deliberately;
- stage new content to a temporary file/directory first when practical;
- validate the staged deployment definition before replacing live files;
- atomically rename staged files into place where the filesystem permits it;
- restrict secret-file permissions, normally `0600`;
- avoid unnecessary plaintext temporary copies;
- do not leave a half-written `.env`, Compose file, or deployment script as live state after an interrupted transfer;
- leave the final runtime files available so the host can restart the deployed stack without GitHub Actions.

## Exact image deployment

Deploy the images produced by the trusted build/publish job.

Prefer digest references:

```text
BACKEND_IMAGE=ghcr.io/<owner>/<backend>@sha256:<digest>
FRONTEND_IMAGE=ghcr.io/<owner>/<frontend>@sha256:<digest>
```

Do not rebuild source on the VDS.

Do not silently replace an intended digest with `latest`.

Authenticate the VDS to GHCR only when registry access requires it, using the least privilege practical.

## Deployment sequence

Use this order where applicable:

```text
1. resolve exact trusted image digests
2. preserve previous deployed image references when rollback is supported
3. sync compose.production.yml / deployment artifacts
4. securely update runtime .env
5. validate Docker Compose configuration
6. authenticate/pull required images
7. ensure required infrastructure is ready
8. run required database migrations
9. update application services
10. wait for bounded container health
11. verify the public application/gateway
12. report deployed revision and image digests
```

Fail immediately on required-step errors.

Do not report deployment success before the public/required health verification succeeds.

## Migrations

Run migrations explicitly before the new backend serves traffic when the project requires them.

A failed migration fails the deployment.

Do not automatically run database downgrade during application rollback.

Application rollback and schema rollback are separate operational decisions.

Prefer backward-compatible schema changes when application rollback must remain possible.

## Health verification

Use bounded polling with a clear timeout.

Verify at the appropriate layers:

- required infrastructure readiness;
- container health;
- backend/application health;
- public gateway/URL smoke check.

Avoid a fixed long sleep as the only readiness mechanism.

Capture enough diagnostics to understand a failed deployment without dumping secrets.

## Rollback

Before replacing a working deployment, preserve the previous immutable application image references when automatic application rollback is supported.

Track rollback state as the last known-good successful deployment, not merely the immediately preceding deployment attempt.

Do not overwrite the last-known-good image references until the new deployment has passed all required service and public health checks. A retry of a failed revision must not erase the identity of the last healthy revision.

If the new application update fails after replacement and application rollback is safe:

```text
restore last-known-good application image references
  -> update services
  -> wait for health
  -> verify public endpoint
  -> report deployment failure and rollback result
```

Rollback success must not hide the original deployment failure.

Do not assume reverting an image undoes:

- database migrations;
- persisted files;
- external side effects;
- queue/messages;
- other stateful changes.

If automatic rollback is unsafe for the project, fail clearly and document the manual recovery procedure instead.

## Deployment concurrency

Allow only one production deployment at a time.

Use GitHub Actions `concurrency` or an equivalent mechanism.

Do not cancel a deployment in a way that can leave migrations/service replacement half-complete unless the workflow is explicitly designed for safe interruption.

## Controlled first deployment

Treat the first production deployment as a verification of the complete contract.

Before enabling automatic deployment from the stable branch, verify that deployment is gated on successful required checks for the exact revision being deployed. CI and deployment must not race independently.

Check:

- Environment secrets/variables are resolved without being printed;
- SSH host verification succeeds;
- deployment user has only required permissions;
- server directory/runtime files are correct;
- GHCR pull succeeds;
- Compose validation succeeds;
- migrations succeed;
- services become healthy;
- public routing works;
- the deployed commit/image digests are observable.

A manual `workflow_dispatch` for the first controlled deployment is acceptable when it still uses a trusted artifact and protected Environment.

## Post-deployment state

The server should be able to restart the already deployed stack without access to the ephemeral GitHub runner.

Production state should make it possible to identify:

- repository revision;
- backend/frontend image digests;
- current Compose definition;
- current runtime configuration location.

Do not persist GitHub runner-only environment assumptions.

## Integration completion criteria

Production integration is complete when:

- protected production Environment configuration is wired correctly;
- SSH deployment uses host verification and least practical privilege;
- exact published images are pulled on the VDS;
- repository Compose/runtime files are synchronized correctly;
- required migrations are explicit and fail safely;
- service/public health checks determine success;
- concurrent deployments are prevented;
- rollback or manual recovery behavior is defined and tested where practical;
- at least one controlled end-to-end production deployment succeeds;
- deployed revision/image identity is observable;
- documentation matches the real deployed process.

Only after these checks may the project claim production CD is operational.
