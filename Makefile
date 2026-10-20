PYTHON ?= python3

.PHONY: fix check

# Safe whitespace normalization of project-owned text; vendored skills preserved.
fix:
	@$(PYTHON) scripts/check_bootstrap.py --fix

# Read-only, offline documentation/YAML/standards validation.
check:
	@$(PYTHON) scripts/check_bootstrap.py
