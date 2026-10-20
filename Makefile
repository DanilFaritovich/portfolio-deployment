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
	@$(PYTHON) -m pytest -q -p no:cacheprovider

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
