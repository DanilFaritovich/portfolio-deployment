"""Internal root guest entry point. Never executes on the developer's Docker host."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys

# Executable by absolute path after a fixed allowlisted source transfer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deployment import host_lock
from scripts.docker_adapter import DockerError
from scripts.e2e.safety import BASE, GuestScope, GuestDocker, private_json, require, session_id
from scripts.e2e.images import Images
from scripts.e2e.gateway import Gateway
from scripts.e2e.cleanup import clean
from scripts.e2e.runner import Runner


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "images", "clean", "clean-plan", "diagnose"))
    parser.add_argument("--session", required=True)
    parser.add_argument("--authorize", required=True)
    parser.add_argument("--gateway-authorize", default="")
    parser.add_argument("--cleanup-approved", default="")
    args = parser.parse_args(argv)
    attempted = False
    try:
        session = session_id(args.session)
        require(args.authorize == f"{args.action}:{session}", "explicit-guest-action-authorization-required")
        identity = private_json(BASE / session / "identity.json")
        require(set(identity) == {"session", "machine", "daemon"} and identity["session"] == session,
                "guest-identity-mismatch")
        scope = GuestScope(session, f"run:{session}", identity["machine"], identity["daemon"])
        adapter = GuestDocker(scope)
        adapter.prerequisites()
        scope.validate()
        os.chdir(Path(__file__).resolve().parents[1])
        source = private_json(Path(__file__).resolve().parents[1] / "source.json")
        from scripts.reconciliation import revision
        git_revision = revision(source["revision"])
        if args.action == "diagnose":
            result = {"status": "passed", "vm": "identity-verified", "daemon": "identity-verified",
                      "report": private_json(scope.directory / "report.json") if (scope.directory / "report.json").exists() else
                                {"realE2E": "not-performed"}}
        elif args.action in ("clean", "clean-plan"):
            baseline = {tuple(r) for r in private_json(scope.directory / "baseline.json")}
            result = clean(scope, adapter, baseline, git_revision, preview=args.action == "clean-plan",
                           approved=json.loads(args.cleanup_approved) if args.cleanup_approved else None)
        else:
            images = Images(Path(__file__).resolve().parents[1] / "images/lock.json")
            if args.action == "images":
                with host_lock():
                    for reference in sorted(images.manifests):
                        # Explicit preparation only. Engine never pulls/builds.
                        adapter._run(["image", "pull", "--platform", "linux/amd64", reference], timeout=180)
                        require(images.evidence(reference, adapter.inspect("image", reference)).verified,
                                "pulled-image-identity-unverified")
                result = {"status": "passed", "images": len(images.manifests), "realE2E": "not-performed"}
            else:
                require(args.gateway_authorize == f"activate:{session}", "explicit-gateway-authorization-required")
                require(not (scope.directory / "registry").exists() and not (scope.directory / "report.json").exists(),
                        "session-already-started-use-diagnose-no-replay")
                gateway = Gateway(scope, authorization=args.gateway_authorize)
                runner = Runner(scope, adapter, images, gateway, git_revision)
                def interrupted(signum, frame):
                    raise KeyboardInterrupt
                signal.signal(signal.SIGTERM, interrupted)
                attempted = True
                result = runner.run()
        print(json.dumps(result, sort_keys=True))
        return 0  # Internal transport always returns JSON; host CLI maps status to exit code.
    except (DockerError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": error.code if isinstance(error, DockerError) else
                          "guest-evidence-or-prerequisites-unavailable",
                          "realE2E": "interrupted-or-blocked" if attempted else "not-performed"}))
        return 0  # Preserve structured blocked diagnostics for the host transport.


if __name__ == "__main__":
    sys.exit(main())
