PYTHON ?= python3
# Preserve machine-readable plan output when Make is invoked recursively.
MAKEFLAGS += --no-print-directory

.PHONY: fix validate check

# Safe whitespace normalization of project-owned text; vendored skills preserved.
fix:
	@$(PYTHON) scripts/check_bootstrap.py --fix

# Read-only validation of the complete registry and admission policy.
validate:
	@$(PYTHON) scripts/validate_registry.py

# Read-only, offline repository, registry and test validation.
check:
	@$(PYTHON) scripts/check_bootstrap.py
	@$(PYTHON) scripts/validate_registry.py
	@$(PYTHON) -m scripts.acceptance_contract
	@$(PYTHON) -m pytest -q -p no:cacheprovider

# Pinned actionlint is provisioned separately, never downloaded by checks.
# Disable optional external linters so results do not depend on runner tools.
.PHONY: workflow-check
ACTIONLINT ?= actionlint
workflow-check:
	@command -v "$(ACTIONLINT)" >/dev/null || { echo "Install actionlint 1.7.7; see docs/stage-08-automation.md" >&2; exit 1; }
	@$(ACTIONLINT) -shellcheck= -pyflakes= .github/workflows/*.yml

# Explicit local fixture inputs; no observed-state default or live discovery.
.PHONY: plan
export OBSERVED GIT_REVISION
PLAN_FORMAT ?= text
export PLAN_FORMAT
plan:
	@$(PYTHON) -c 'import os,sys; from scripts.plan_registry import main; sys.exit(main(["--observed",os.environ.get("OBSERVED",""),"--git-revision",os.environ.get("GIT_REVISION",""),"--format",os.environ["PLAN_FORMAT"]]))'

# Stage 04 remains offline; official validation is opt-in and requires local Caddy.
.PHONY: gateway-render gateway-check gateway-validate
GATEWAY_FORMAT ?= json
REGISTRY_ROOT ?= .
export GATEWAY_FORMAT REGISTRY_ROOT
gateway-render:
	@$(PYTHON) -c 'import os,sys; from scripts.gateway_cli import main; sys.exit(main(["--root",os.environ["REGISTRY_ROOT"],"--observed",os.environ.get("OBSERVED",""),"--git-revision",os.environ.get("GIT_REVISION",""),"--format",os.environ["GATEWAY_FORMAT"]]))'

gateway-check:
	@$(PYTHON) -m pytest -q -p no:cacheprovider tests/test_gateway.py

gateway-validate:
	@$(PYTHON) -c 'import os,sys; from scripts.gateway_cli import main; sys.exit(main(["--root",os.environ["REGISTRY_ROOT"],"--observed",os.environ.get("OBSERVED",""),"--git-revision",os.environ.get("GIT_REVISION",""),"--format","json","--official"]))'

# Stage 05 previews and tests remain offline. No implicit application selection.
.PHONY: compose-render deployment-plan compose-check compose-validate local-docker-smoke
COMPOSE_FORMAT ?= json
export APPLICATION COMPOSE_FORMAT
compose-render:
	@$(PYTHON) -c 'import os,sys; from scripts.deployment_cli import main; sys.exit(main(["compose-render","--root",os.environ["REGISTRY_ROOT"],"--application",os.environ.get("APPLICATION",""),"--git-revision",os.environ.get("GIT_REVISION",""),"--format",os.environ["COMPOSE_FORMAT"]]))'

deployment-plan:
	@$(PYTHON) -c 'import os,sys; from scripts.deployment_cli import main; sys.exit(main(["deployment-plan","--root",os.environ["REGISTRY_ROOT"],"--application",os.environ.get("APPLICATION",""),"--git-revision",os.environ.get("GIT_REVISION",""),"--observed",os.environ.get("OBSERVED",""),"--format","json"]))'

compose-check:
	@$(PYTHON) -m pytest -q -p no:cacheprovider tests/test_deployment.py

# Official local syntax validation only; separate from the offline compose-check.
compose-validate:
	@$(PYTHON) -c 'import os,sys; from scripts.deployment_cli import main; sys.exit(main(["compose-validate","--root",os.environ["REGISTRY_ROOT"],"--application",os.environ.get("APPLICATION",""),"--git-revision",os.environ.get("GIT_REVISION","")]))'

export SMOKE_ENDPOINT SMOKE_SESSION SMOKE_IMAGE_A SMOKE_IMAGE_B SMOKE_AUTHORIZE
local-docker-smoke:
	@$(PYTHON) -c 'import os,sys; from scripts.local_docker_smoke import main; sys.exit(main(["--endpoint",os.environ.get("SMOKE_ENDPOINT",""),"--session",os.environ.get("SMOKE_SESSION",""),"--image-a",os.environ.get("SMOKE_IMAGE_A",""),"--image-b",os.environ.get("SMOKE_IMAGE_B",""),"--authorize",os.environ.get("SMOKE_AUTHORIZE","")]))'

# Stage 07: e2e bootstraps missing fixtures with separate interactive approvals.
# e2e-fixtures prepares fixtures alone; CLI --plan/--manifests stay offline.
# e2e-report is local-only; all configuration variables are optional overrides.
# e2e-check remains offline. VM/image/run/cleanup opt-ins are separate.
.PHONY: e2e e2e-fixtures e2e-report
export E2E_FIXTURE_EXECUTE
export E2E_STATE_DIR E2E_FIXTURE_DIR E2E_FIXTURE_OWNER E2E_PYTHON_BASE E2E_SOURCE_DATE_EPOCH E2E_RAW_MANIFESTS
e2e:
	@$(PYTHON) -m scripts.e2e.cli run

e2e-fixtures:
	@$(PYTHON) -m scripts.e2e.cli fixtures

e2e-report:
	@$(PYTHON) -m scripts.e2e.cli report

.PHONY: e2e-check e2e-prerequisites e2e-prepare e2e-status e2e-images e2e-test e2e-diagnose e2e-clean
export E2E_SESSION E2E_AUTHORIZE E2E_GATEWAY_AUTHORIZE E2E_IMAGES
e2e-check:
	@$(PYTHON) -m pytest -q -p no:cacheprovider tests/test_local_e2e.py tests/test_e2e_fixtures.py tests/test_e2e_bootstrap.py tests/test_e2e_paths.py tests/test_e2e_control.py tests/test_e2e_transport.py tests/test_e2e_builder_recovery.py tests/test_e2e_prepared_recovery.py tests/test_e2e_failed_run_recovery.py

e2e-prerequisites:
	@$(PYTHON) -m scripts.e2e.cli prerequisites

e2e-prepare:
	@$(PYTHON) -m scripts.e2e.cli prepare

e2e-status:
	@$(PYTHON) -m scripts.e2e.cli status

e2e-images:
	@$(PYTHON) -m scripts.e2e.cli images

e2e-test:
	@$(PYTHON) -m scripts.e2e.cli test

e2e-diagnose:
	@$(PYTHON) -m scripts.e2e.cli diagnose

e2e-clean:
	@$(PYTHON) -m scripts.e2e.cli clean

# Explicit read-only inventory and narrowly scoped operator closure; never replay.
.PHONY: e2e-fixture-diagnose e2e-close-unknown e2e-review-failed-builder e2e-close-failed-builder
export E2E_EVIDENCE
e2e-fixture-diagnose:
	@$(PYTHON) -m scripts.e2e.cli fixture-diagnose

e2e-close-unknown:
	@$(PYTHON) -m scripts.e2e.cli close-unknown

# Incident-specific read-only review while Running, closure only after operator stop.
.PHONY: e2e-interrupt-prepared e2e-continue-prepared e2e-close-failed-bootstrap e2e-close-failed-run
export E2E_CONTROLLER_PID
e2e-interrupt-prepared:
	@$(PYTHON) -m scripts.e2e.cli interrupt-prepared

e2e-continue-prepared:
	@$(PYTHON) -m scripts.e2e.cli continue-prepared

e2e-close-failed-bootstrap:
	@$(PYTHON) -m scripts.e2e.cli close-failed-bootstrap

e2e-close-failed-run:
	@$(PYTHON) -m scripts.e2e.cli close-failed-run

e2e-review-failed-builder:
	@$(PYTHON) -m scripts.e2e.cli review-failed-builder

e2e-close-failed-builder:
	@$(PYTHON) -m scripts.e2e.cli close-failed-builder

# Stage 09.2: local prerequisites/report are offline; inspect requires a terminal.
.PHONY: production-check production-inspect production-report
PRODUCTION_INVENTORY ?= ../portfolio-server-infrastructure/inventories/production.yml
export PRODUCTION_INVENTORY
production-check:
	@$(PYTHON) -m scripts.production.cli check

production-inspect:
	@$(PYTHON) -m scripts.production.cli inspect

production-report:
	@$(PYTHON) -m scripts.production.cli report

# Stage 09.3: saved-input previews only; no SSH, socket, downloads or apply.
.PHONY: production-plan production-preflight production-integration-check
export PRODUCTION_CONTRACT
production-preflight:
	@$(PYTHON) -c 'import os,sys; from scripts.production_integration.cli import main; sys.exit(main(["preflight","--contract",os.environ.get("PRODUCTION_CONTRACT","")]))'

production-plan:
	@$(PYTHON) -c 'import os,sys; from scripts.production_integration.cli import main; sys.exit(main(["plan","--contract",os.environ.get("PRODUCTION_CONTRACT",""),"--root",os.environ["REGISTRY_ROOT"],"--git-revision",os.environ.get("GIT_REVISION",""),"--observed",os.environ.get("OBSERVED","")]))'

production-integration-check:
	@$(PYTHON) -m pytest -q -p no:cacheprovider tests/test_production_integration.py tests/test_production_ports.py tests/test_production_preparation.py tests/test_release_collector.py tests/test_production_execution.py

# Candidate declarations only: never Docker, registry requests or workflow dispatch.
.PHONY: acceptance-check
acceptance-check:
	@$(PYTHON) -m scripts.acceptance_contract

# Real Engine on an explicitly authorized, empty disposable local daemon only.
# Never part of offline check or application acceptance; never publishes ports.
.PHONY: runtime-integration-test
export RUNTIME_TEST_ENDPOINT RUNTIME_TEST_AUTHORIZE
runtime-integration-test:
	@$(PYTHON) -c 'import os,sys; from scripts.runtime_integration import main; sys.exit(main(["--endpoint",os.environ.get("RUNTIME_TEST_ENDPOINT",""),"--authorize",os.environ.get("RUNTIME_TEST_AUTHORIZE","")]))'

# Explicitly authorized ephemeral GitHub-hosted PR runner only; no host ports.
.PHONY: caddy-integration-test
export CADDY_TEST_ENDPOINT CADDY_TEST_AUTHORIZE
caddy-integration-test:
	@$(PYTHON) -c 'import os,sys; from scripts.caddy_integration import main; sys.exit(main(["--endpoint",os.environ.get("CADDY_TEST_ENDPOINT",""),"--authorize",os.environ.get("CADDY_TEST_AUTHORIZE","")]))'
