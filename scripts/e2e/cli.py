"""Unified Stage 07 CLI: offline fixtures/status and separately confirmed VM run."""
import argparse
import json
import os
from pathlib import Path
import sys

from scripts.deployment_journal import JournalError
from scripts.docker_adapter import DockerError
from scripts.e2e import fixture_bundle, fixture_execution, orchestration, vm
from scripts.e2e.host_state import Store

LEGACY = ("prerequisites", "prepare", "status", "images", "test", "clean", "diagnose")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in LEGACY:
        return vm.main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "fixtures", "report"))
    parser.add_argument("--state-dir", default=os.environ.get("E2E_STATE_DIR") or ".e2e-state")
    parser.add_argument("--images", default=os.environ.get("E2E_IMAGES", ""))
    parser.add_argument("--output", default=os.environ.get("E2E_FIXTURE_DIR") or ".e2e-fixtures")
    parser.add_argument("--owner", default=os.environ.get("E2E_FIXTURE_OWNER", ""))
    parser.add_argument("--base", default=os.environ.get("E2E_PYTHON_BASE", ""))
    parser.add_argument("--epoch", type=int, default=os.environ.get("E2E_SOURCE_DATE_EPOCH") or 0)
    parser.add_argument("--manifests", default=os.environ.get("E2E_RAW_MANIFESTS") or None)
    parser.add_argument("--execute", action="store_true", default=os.environ.get("E2E_FIXTURE_EXECUTE") == "yes")
    args = parser.parse_args(argv)
    try:
        if args.action == "run":
            result = orchestration.run(args.images or str(Path(args.output) / "artifacts/lock.json"), args.state_dir)
        elif args.action == "fixtures":
            if args.execute:
                if args.manifests:
                    raise ValueError("execute-and-import-are-mutually-exclusive")
                result = fixture_execution.execute(args.output, args.owner, args.base, epoch=args.epoch)
            else:
                result = fixture_bundle.prepare(args.output, args.owner, args.base, epoch=args.epoch, manifests=args.manifests)
        else:
            with Store(args.state_dir).locked() as store:
                result = orchestration.current(store) or {"status": "blocked", "reason": "no-local-e2e-receipt"}
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except (DockerError, JournalError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": error.code if isinstance(error, DockerError)
                          else "e2e-prerequisite-or-host-state-invalid", "realE2E": "not-performed-by-this-call"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
