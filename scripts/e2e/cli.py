"""Unified Stage 07 CLI: offline fixtures/status and separately confirmed VM run."""
import argparse
import json
import os
from pathlib import Path
import sys

try:
    from scripts.deployment_journal import JournalError
    from scripts.docker_adapter import DockerError
    from scripts.e2e import bootstrap, control, fixture_bundle, fixture_execution, orchestration, paths, vm
    from scripts.e2e.host_state import Store
    from scripts.e2e.multipass import evidence
    from scripts.e2e.images import Images
    from scripts.e2e.safety import require
except ModuleNotFoundError as error:
    print(json.dumps({"status": "blocked", "reason": "missing-python-dependency", "missing": error.name,
                      "help": "Install pinned dependencies with your selected Python: python -m pip install -r requirements-dev.txt; then run make e2e PYTHON=<that-python>."}))
    raise SystemExit(2) from None

LEGACY = ("prerequisites", "prepare", "status", "images", "test", "clean", "diagnose")


def fixtures(args):
    owner = args.owner
    if args.plan or args.manifests:
        require(bool(args.base), "offline-plan-needs-immutable-base")
        return fixture_bundle.prepare(args.output, owner, args.base, epoch=args.epoch, manifests=args.manifests)
    with Store(args.output).locked() as store:
        receipt = store.read("execution.json")
        plan = store.read("plan.json")
        base = args.base or (plan["base"] if plan else "")
        if receipt is not None:
            require(receipt.get("status") == "passed" and receipt.get("phase") == "complete",
                    "fixture-execution-unfinished-no-retry")
            images = Images(store.path / "artifacts/lock.json")
            require(images.records == receipt["references"], "published-fixture-lock-changed")
    if receipt is not None:
        fixture_bundle.prepare(args.output, owner, base, epoch=args.epoch)
        return receipt
    orchestration.confirm(
        "Phase: fixture-plan; session: not-created; VM: not-created.\n"
        "Prepare six fixture images in separate disposable builder/publisher VMs. "
        "First resolve anonymous official Python manifest/config metadata and pin linux/amd64 locally. "
        "VM creation, build, GHCR publication and public verification each require separate confirmation.",
        "prepare-fixtures")
    base = bootstrap.resolve_base(args.output, base)
    return fixture_execution.execute(args.output, owner, base, epoch=args.epoch)


def guidance(reason):
    if reason == "e2e-legacy-state-review-required":
        return "Legacy .e2e-state or .e2e-fixtures needs manual review. Stop concurrent controllers; inspect receipts and pinned VM identity locally. Resolve unfinished/unknown operations before migration; never retry or reset them. See docs/local-e2e.md legacy transition. No files were moved or deleted."
    if reason.startswith("e2e-path-") or reason == "unsafe-e2e-host-state":
        return "Use owned private state directories (0700; files 0600) below ~/.local/share/portfolio-deployment, or safe E2E_STATE_DIR/E2E_FIXTURE_DIR overrides. Every ancestor must be owned by root/current user and not group/world writable; symlinks and traversal are forbidden. Do not chmod HOME, Work or the Git checkout. Existing unsafe directories are never repaired automatically."
    if "origin" in reason or "owner" in reason:
        return "Set a single GitHub origin fetch/push URL for this repository (git remote -v); use E2E_FIXTURE_OWNER only to override the target GHCR owner."
    if "base" in reason or "python3" in reason:
        return "Check HTTPS access to auth.docker.io and registry-1.docker.io and e2e/python-base.json. E2E_PYTHON_BASE accepts an official docker.io/library/python@sha256 direct linux/amd64 digest. Retain any existing pin; use a new private directory for changed inputs."
    if "staging" in reason or "snap" in reason:
        return "Inspect retained ~/PortfolioDeploymentE2E/<session>/ staging and private receipts. Check snap connections multipass: home must be connected; if disconnected an operator may authorize sudo snap connect multipass:home. Do not use symlinks or chmod HOME. Read-only Snap access/hash probe must pass before launch/transfer. No retry or cleanup was attempted."
    if "unfinished" in reason or "closure" in reason:
        return "Stop controllers and inspect original receipts and Multipass operations/inventory independently. Use fixture-diagnose and the narrow close-unknown procedure in docs/local-e2e.md. Keep locks and evidence; changing directories never authorizes retry."
    if "prerequisite" in reason or "multipass" in reason:
        return "Install/authenticate Multipass; use Linux amd64 with accessible /dev/kvm, 64 GiB free disk for fixtures (20 GiB for E2E), and capacity for 2 CPU/4 GiB per VM. Check make e2e-prerequisites; allow Ubuntu/package/registry network access."
    if "confirmation" in reason or "declined" in reason:
        return "Run make e2e in a real interactive terminal and answer y/yes separately only for the displayed operations you authorize. Inspect existing receipts before continuing."
    if "lock" in reason or "image" in reason or "manifest" in reason:
        return "Check the lock and its six exact raw manifests; correct E2E_IMAGES or prepare fixtures with make e2e-fixtures in a new private directory. No rebuild or retry was attempted."
    return "Inspect make e2e-report and private execution.json/session receipts; for a VM use the printed e2e-diagnose command. Resolve missing files/permissions or unfinished state manually; no automatic retry, reset or cleanup is performed."


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in LEGACY:
        return vm.main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "fixtures", "report", "fixture-diagnose", "close-unknown",
                                          "review-failed-builder", "close-failed-builder",
                                          "interrupt-prepared", "continue-prepared"))
    parser.add_argument("--pid", type=int, default=int(os.environ.get("E2E_CONTROLLER_PID") or 0))
    parser.add_argument("--session", default=os.environ.get("E2E_SESSION", ""))
    parser.add_argument("--evidence", default=os.environ.get("E2E_EVIDENCE", ""))
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--images", default=os.environ.get("E2E_IMAGES", ""))
    parser.add_argument("--output", default=None)
    parser.add_argument("--owner", default=os.environ.get("E2E_FIXTURE_OWNER", ""))
    parser.add_argument("--base", default=os.environ.get("E2E_PYTHON_BASE", ""))
    parser.add_argument("--epoch", type=int, default=os.environ.get("E2E_SOURCE_DATE_EPOCH") or 0)
    parser.add_argument("--manifests", default=os.environ.get("E2E_RAW_MANIFESTS") or None)
    parser.add_argument("--execute", action="store_true", default=os.environ.get("E2E_FIXTURE_EXECUTE") == "yes")
    parser.add_argument("--plan", action="store_true", help="offline plan only; requires an explicit immutable --base")
    args = parser.parse_args(argv)
    try:
        paths.check_legacy()
        args.state_dir = paths.resolve("state", args.state_dir)
        args.output = paths.resolve("fixtures", args.output)
        if args.action in ("run", "fixtures") and not (args.plan or args.manifests):
            control.preflight()
        if args.action in ("run", "fixtures"):
            args.owner = bootstrap.owner_from_origin(args.owner)
        if args.action == "run":
            # Never build fixtures while an earlier E2E mutation is unresolved.
            with Store(args.state_dir).locked() as store:
                previous = orchestration.current(store)
                require(previous is None or previous["status"] == "passed" or not previous["mutationStarted"],
                        "previous-e2e-unfinished-diagnose-required")
            lock = args.images or str(Path(args.output) / "artifacts/lock.json")
            if not args.images:
                with Store(args.output).locked() as store:
                    receipt = store.read("execution.json")
                    require(receipt is None or receipt.get("status") == "passed" and receipt.get("phase") == "complete",
                            "fixture-execution-unfinished-no-retry")
                    if receipt is not None:
                        require(Images(lock).records == receipt["references"], "published-fixture-lock-changed")
                if not os.path.lexists(lock):
                    result = fixtures(args)
                    if result["status"] != "passed":
                        result["help"] = guidance(result.get("reason", "fixture-incomplete"))
                        print(json.dumps(result, sort_keys=True))
                        return 2
            Images(lock)  # Invalid existing locks fail closed, never trigger a rebuild.
            result = orchestration.run(lock, args.state_dir)
        elif args.action in ("interrupt-prepared", "continue-prepared"):
            from scripts.e2e import prepared_recovery
            result = (prepared_recovery.stop(args.state_dir, args.session, args.pid)
                      if args.action == "interrupt-prepared" else
                      prepared_recovery.continue_prepared(args.state_dir, args.session))
        elif args.action == "fixture-diagnose":
            result = control.diagnose(args.output, args.session)
        elif args.action == "close-unknown":
            result = control.close_unknown(args.output, args.session, args.evidence)
        elif args.action in ("review-failed-builder", "close-failed-builder"):
            from scripts.e2e import builder_recovery
            if args.action == "review-failed-builder":
                result = builder_recovery.collect(args.output, args.session)
            else:
                result = builder_recovery.close(args.output, args.session, args.evidence)
        elif args.action == "fixtures":
            require(not (args.execute and (args.manifests or args.plan)), "execute-and-import-are-mutually-exclusive")
            result = fixtures(args)
        else:
            with Store(args.state_dir).locked() as store:
                result = orchestration.current(store) or {"status": "blocked", "reason": "no-local-e2e-receipt"}
        if result["status"] != "passed":
            result["help"] = guidance(result.get("reason", result.get("result", {}).get("reason", "incomplete")))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except (DockerError, JournalError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({"status": "blocked", "reason": error.code if isinstance(error, DockerError)
                          else str(error) if isinstance(error, JournalError) and str(error).startswith("e2e-path-")
                          else "e2e-prerequisite-or-host-state-invalid",
                          "help": guidance(error.code if isinstance(error, DockerError) else str(error) if isinstance(error, JournalError) else "invalid-state"),
                          "realE2E": "not-performed-by-this-call", **evidence(error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
