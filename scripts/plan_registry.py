"""Offline registry planning CLI. No discovery, Git lookup or execution."""

import argparse
from pathlib import Path
import sys

from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan
from scripts.validate_registry import ROOT, RegistryValidationError


class InputParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may echo arbitrary rejected input values.
        self.exit(1, "Input error: require --observed PATH --git-revision FULL_SHA; --format text|json\n")


def main(argv=None):
    parser = InputParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--git-revision", required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    options = parser.parse_args(argv)
    try:
        desired = build_desired_state(options.root, options.git_revision)
        observed = load_observed_state(options.observed)
        plan = build_reconciliation_plan(desired, observed)
    except (PlanningInputError, RegistryValidationError) as error:
        print(f"Input error: {error}", file=sys.stderr)
        return 1
    print(plan.to_json() if options.format == "json" else plan.to_text(), end="")
    return {"blocked": 2, "degraded": 3}.get(plan.document["status"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
