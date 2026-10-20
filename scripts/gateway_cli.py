"""Explicit-input offline gateway preview; never applies a configuration."""

import argparse
from pathlib import Path
import sys

from scripts.gateway import GatewayRoute, build_gateway_candidate
from scripts.gateway_validation import validate_with_caddy
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan, encode
from scripts.validate_registry import ROOT, RegistryValidationError


class InputParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, "Input error: require --observed PATH --git-revision FULL_SHA; --format json|caddyfile\n")


def main(argv=None):
    parser = InputParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--git-revision", required=True)
    parser.add_argument("--format", choices=("json", "caddyfile"), default="json")
    parser.add_argument("--official", action="store_true", help="validate retained full candidate with local Caddy")
    args = parser.parse_args(argv)
    try:
        desired = build_desired_state(args.root, args.git_revision)
        observed = load_observed_state(args.observed)
        candidate = build_gateway_candidate(desired, observed, build_reconciliation_plan(desired, observed))
    except (PlanningInputError, RegistryValidationError):
        print("Input error: registry, snapshot or gateway invariants invalid", file=sys.stderr)
        return 1
    document = candidate.document
    official_failed = False
    if args.official:
        if document["candidate"] is None:
            document["officialValidation"] = {"status": "not-performed", "reason": "no-complete-candidate"}
        else:
            routes = tuple(GatewayRoute(**r) for r in document["candidate"]["routes"])
            document["officialValidation"] = validate_with_caddy(routes)
        official_failed = document["officialValidation"]["status"] != "passed"
    if args.format == "json":
        print(encode(document))
    elif document["candidate"] is None or document["candidate"]["caddyfile"] is None:
        print("No renderable full candidate; inspect JSON outcome", file=sys.stderr)
        return 2 if document["status"] == "blocked" else 3
    else:
        print(document["candidate"]["caddyfile"], end="")
        if official_failed:
            print("Official Caddy validation failed or was not performed; inspect JSON report", file=sys.stderr)
    return 2 if document["status"] == "blocked" else 4 if official_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
