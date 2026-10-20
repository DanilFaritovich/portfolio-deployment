"""Offline Compose/deployment previews; no apply or implicit Docker context."""

import argparse
import json
import sys

import yaml

from scripts.compose import render_compose
from scripts.deployment import deployment_preview
from scripts.docker_adapter import DockerError, official_validate
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan, encode
from scripts.validate_registry import RegistryValidationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compose-render", "deployment-plan", "compose-validate"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--application", required=True)
    parser.add_argument("--git-revision", required=True)
    parser.add_argument("--observed")
    parser.add_argument("--format", choices=("json", "yaml"), default="json")
    args = parser.parse_args(argv)
    try:
        desired = build_desired_state(args.root, args.git_revision)
        app = next((a for a in desired.applications if a.id == args.application), None)
        if app is None:
            raise PlanningInputError("unknown-application")
        artifact = render_compose(app)
        if args.command == "compose-render":
            print(artifact.to_json(), end="") if args.format == "json" else print(yaml.safe_dump(artifact.model, sort_keys=True), end="")
            return 0
        if args.command == "compose-validate":
            result = official_validate(artifact)
            print(encode(result))
            return 0 if result["status"] == "passed" else 4
        if not args.observed or args.format != "json":
            raise PlanningInputError("explicit-observed-and-json-required")
        observed = load_observed_state(args.observed)
        plan = build_reconciliation_plan(desired, observed)
        ops = deployment_preview(desired, observed, plan, app.id)
        print(encode({"apiVersion": "portfolio.deployment-preview/v1", "gitRevision": desired.git_revision,
                      "application": app.id, "composeSha256": artifact.sha256, "project": artifact.project,
                      "status": plan.document["status"], "executed": False, "authorization": False,
                      "plan": plan.document, "operations": [o.__dict__ for o in ops],
                      "requiredChecks": ["fresh-full-observation", "endpoint-scope-authorization", "ownership",
                                         "oci-mapping", "runtime-reference-resolution", "host-lock", "preflight"]}))
        return {"blocked": 2, "degraded": 3}.get(plan.document["status"], 0)
    except (PlanningInputError, RegistryValidationError, DockerError):
        print("invalid-or-unavailable-deployment-input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
