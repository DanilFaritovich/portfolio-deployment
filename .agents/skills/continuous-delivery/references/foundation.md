# Continuous Delivery Foundation

Read this reference when preparing a project for production CD before a live VDS and production credentials are available.

The goal is to make the repository deployment-ready and produce an explicit infrastructure contract for the developer.

## Scope boundary

This phase may change repository-controlled deployment artifacts.

It must not require:

- a real production server;
- real SSH credentials;
- real production API keys/passwords;
- a populated GitHub `production` Environment;
- a successful real production deploy.

Do not invent secret values or claim external infrastructure was verified.

## Expected repository outputs

Prepare only the artifacts applicable to the project, commonly:

```text
compose.production.yml
.github/workflows/deploy.yml
deploy/...
Makefile
.env.example
docs/deployment.md
```

Existing equivalent locations may be preserved.

Avoid adding parallel abstractions merely to match these names.

## Production Compose

Keep production Compose in Git and free of secrets.

Application services should normally reference externally built images:

```yaml
services:
  backend:
    image: ${BACKEND_IMAGE}

  frontend:
    image: ${FRONTEND_IMAGE}
```

Do not use `build:` for normal production application deployment when images are published by GitHub Actions.

Preserve required persistent volumes.

Keep backend/database/Redis internal unless a host port is operationally required.

When the deployment will later join an existing shared reverse-proxy/project network, model that explicitly instead of exposing unnecessary ports.

## Image build and registry contract

Prepare GitHub Actions to build production application images outside the VDS.

For GitHub-hosted portfolio projects, GHCR is the normal registry.

Use clear image names such as:

```text
ghcr.io/<owner>/<project>-backend
ghcr.io/<owner>/<project>-frontend
```

Publish a commit-SHA tag when useful for traceability and capture immutable digests for deployment.

Do not make `latest` the production source of truth.

Pull Request validation may build images without publishing them.

Publishing must occur only from a trusted source with the required package permissions.

## Deployment workflow foundation

A foundation workflow may define the eventual production pipeline even when the production job cannot yet run without Environment configuration.

The workflow should clearly separate:

```text
validate/build
  -> publish trusted images
  -> protected production deployment
```

Use least-privilege permissions.

Do not expose deployment secrets to Pull Request jobs.

Use production concurrency so two deployments cannot modify the server simultaneously.

A manual `workflow_dispatch` may be prepared for controlled redeploy/recovery, but it must not permit arbitrary untrusted artifacts.

Before production integration is complete, do not enable an unconditional automatic deployment from every `main` push unless the external production Environment is already intentionally ready. Prefer one of:

- manual `workflow_dispatch` for the foundation;
- an explicit disabled-by-default Environment/repository variable gate such as `DEPLOY_ENABLED`;
- adding the automatic production trigger only during the production-integration phase.

The foundation may prepare the complete deployment job without making it operational by accident.

## Keep deployment logic maintainable

Avoid a large opaque shell program inside workflow YAML.

Prefer:

```text
.github/workflows/deploy.yml
        |
        v
Makefile / version-controlled deploy script
        |
        v
Docker Compose
```

Add project-owned commands where they improve reproducibility, for example:

- production Compose validation;
- image build validation;
- migration invocation;
- bounded health wait;
- public smoke check.

Do not add commands merely to create abstraction with no reuse.

## Workflow and script validation

Treat deployment orchestration as executable code.

When GitHub Actions workflows or deployment shell scripts change:

- validate workflow syntax/semantics with `actionlint` or an equivalent pinned/provisioned checker;
- validate shell scripts with `shellcheck` when applicable, in addition to shell syntax checks;
- ensure CI installs/provisions required validation tools instead of silently skipping them because they are absent on one developer machine;
- check that every shell variable referenced by a workflow step is either declared in that step/job environment, assigned in the script, or intentionally defaulted;
- validate required GitHub Environment variables/secrets in a step that actually receives those values;
- fail closed on missing deployment inputs.

A local "tool unavailable: skipped" result is not sufficient final validation for newly introduced production orchestration; the repository CI must perform the deterministic check.

## Migration hook

Prepare an explicit migration command for deployment when the project uses schema migrations.

For Alembic projects, use the project's stable Alembic command.

A failed required migration must fail deployment.

For file-backed databases or other persistent state where downgrade/recovery is not trivial, define a backup/snapshot step or an explicit recovery boundary before potentially destructive production migrations.

Do not move migrations into uncontrolled application startup.

Do not implement automatic database downgrade as application rollback.

If the current project has no migration requirement, document that rather than inventing one.

## Health contract

Production services must expose enough health information for automated deployment verification.

Prepare:

- container health checks where practical;
- bounded readiness waiting;
- a public or gateway-level smoke check;
- clear failure behavior.

Do not use unbounded loops or arbitrary long sleeps.

## Runtime configuration contract

Keep a safe `.env.example` that documents required variable names without real values.

Define how production runtime configuration will later be supplied.

For a small single-VDS project, a normal model is:

```text
GitHub Environment: production
  -> PRODUCTION_ENV_FILE (multiline secret)
  -> /opt/<project>/.env
```

Individual Environment Secrets/Variables may be used instead when they are more maintainable.

The foundation phase defines the contract but does not require real values.

## Server contract

Document the exact one-time prerequisites the developer must prepare on the VDS.

Typical contract:

- Linux VDS;
- Docker Engine;
- Docker Compose plugin;
- dedicated deployment user;
- stable directory such as `/opt/<project>/`;
- ability for that user to manage the project containers;
- SSH key-based access;
- known host key available for GitHub Actions;
- required public reverse proxy/DNS/firewall setup;
- access to GHCR when images are private;
- non-conflicting Docker network/subnet planning when the project uses fixed networks;
- a narrowly defined trusted-proxy source when forwarded client IPs affect security controls or rate limiting.

Do not require root SSH if a less-privileged user can deploy safely.

Document that membership in the host Docker group or unrestricted Docker socket access is effectively host-level privilege. A "non-root deploy user" is not strongly isolated merely because SSH login itself is non-root. Use rootless Docker or a more constrained deployment mechanism when stronger isolation is required.

When a shared proxy network is used, do not blindly trust the entire broad network CIDR for forwarded client identity if the exact reverse-proxy address/range can be trusted instead.

## GitHub Environment contract

Document the exact production Environment inputs the integration phase will require.

Typical names:

- `DEPLOY_HOST`;
- `DEPLOY_USER`;
- `DEPLOY_SSH_KEY`;
- `SSH_KNOWN_HOSTS`;
- `PRODUCTION_ENV_FILE`.

Names may differ by project; document the actual contract used by the workflow.

Distinguish secrets from non-sensitive variables.

Never put example real secret values in repository documentation.

## Foundation validation

Validate what is possible without production access.

Useful checks include:

- production Docker image build;
- `docker compose -f compose.production.yml config`;
- deployment script syntax;
- migration command viability against a test environment when justified;
- reverse-proxy config validation;
- service health smoke tests in a local/test stack when reasonable.

Do not attempt a fake production SSH deployment merely to satisfy validation.

## Foundation completion criteria

Foundation is complete when:

- production Compose is version-controlled and secret-free;
- production application services consume published-image references;
- trusted image build/publish behavior is defined;
- deployment orchestration is version-controlled;
- migration and health hooks are defined;
- Pull Request CI can validate relevant deployment artifacts;
- required VDS prerequisites are documented;
- required GitHub Environment inputs are documented;
- no real production secret is committed;
- the developer has a concrete checklist for external setup.

At completion, report the external prerequisites still required before production integration.

Do not claim production CD is complete at this phase.
