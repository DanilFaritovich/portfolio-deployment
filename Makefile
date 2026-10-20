PYTHON ?= python3

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
