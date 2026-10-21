"""Fixed host-wide fence and narrow evidence-based builder preparation closure.

Original receipts and locks are immutable evidence for closure. A closure never
resumes a phase; only a separately confirmed new session may follow.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

from scripts.e2e.host_state import Store
from scripts.e2e.safety import require, session_id
from scripts.planning_inputs import parse_snapshot
from scripts.e2e.multipass import MultipassError


def root():
    return Path.home() / ".local/share/portfolio-deployment/e2e-control"


def receipt(store, kind):
    if kind == "fixtures":
        return store.read("execution.json")
    pointer = store.read("current.json")
    return store.read(session_id(pointer["session"]) + ".json") if pointer else None


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def closed(control, state, path):
    if state is None or not re.fullmatch(r"[0-9a-f]{16}", str(state.get("session", ""))):
        return False
    decision = control.read("closed-" + state["session"] + ".json")
    return decision is not None and decision.get("receiptSha256") == digest(state) and decision.get("directory") == str(path)


def check(control, path, kind):
    if not os.path.lexists(path):
        return
    with Store(path).locked() as store:
        state = receipt(store, kind)
        require(not any(name.endswith(".pending") for name in os.listdir(store.fd)), "e2e-pending-evidence-review-required")
        if state is not None:
            terminal = state.get("status") == "passed" and state.get("phase") == "complete"
            declined = kind == "state" and state.get("mutationStarted") is False
            require(terminal or declined or closed(control, state, store.path), "e2e-global-unfinished-review-required")


def check_all(control):
    require(not any(name.endswith(".pending") for name in os.listdir(control.fd)), "e2e-control-pending-review-required")
    # Existing-builder decisions retain their source directory as durable evidence.
    for name in os.listdir(control.fd):
        if re.fullmatch(r"closed-[0-9a-f]{16}\.json", name):
            decision = control.read(name)
            if decision.get("decision") == "closed-stopped-failed-builder-no-replay":
                from scripts.e2e.builder_recovery import verify_retained
                verify_retained(decision, control)
    # Fixed defaults are checked regardless of overrides: legacy unknown cannot
    # be bypassed by selecting a fresh output/state directory.
    base = Path.home() / ".local/share/portfolio-deployment"
    for path, kind in ((base / "e2e-fixtures", "fixtures"), (base / "e2e-state", "state")):
        check(control, path, kind)
    active = control.read("active.json")
    if active:
        path = Path(active["directory"])
        require(os.path.lexists(path), "e2e-tracked-evidence-missing")
        check(control, path, active["kind"])


@contextmanager
def guard(path, kind):
    with Store(root()).locked() as control:
        check_all(control)
        # A durable path must exist before registration. With no receipt yet,
        # callers cannot have dispatched VM mutations (intent precedes dispatch).
        with Store(path).locked() as selected:
            state = receipt(selected, kind)
            require(not closed(control, state, selected.path), "e2e-closed-session-no-replay-use-new-directory")
        # Register before the caller can mutate; retain the fixed lock inode.
        control.write("active.json", {"directory": str(Path(path).absolute()), "kind": kind})
        yield control


def preflight():
    with Store(root()).locked() as control:
        check_all(control)


def inventory(machine):
    try:
        value = json.loads(machine.command("list", "--format", "json"))
        require(isinstance(value, dict) and isinstance(value.get("list"), list) and len(value["list"]) <= 512 and
                all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in value["list"]),
                "multipass-list-invalid-response")
        return value
    except (ValueError, TypeError, KeyError):
        raise MultipassError("list", "invalid-response") from None


def diagnose(path, session, *, factory=None):
    """Explicit read-only VM inventory plus local fixture receipt; no guest exec."""
    from scripts.e2e.vm import VM
    session_id(session)
    with Store(path).locked() as store:
        state = store.read("execution.json")
        require(state is not None and state.get("session") == session, "e2e-receipt-session-mismatch")
        listing = inventory((factory or VM)(session))
        # Only bounded safe identity/state fields, never raw addresses or errors.
        vms = [{"name": item["name"], "state": item.get("state", "Unknown")}
               for item in listing["list"] if item.get("name") in (state["builder"], state["publisher"])]
        return {"status": "passed", "receipt": state, "receiptSha256": digest(state), "vms": vms,
                "phase": state["phase"], "session": session, "readOnly": True}


def close_unknown(path, session, evidence_path, *, consent=None, factory=None):
    from scripts.e2e.orchestration import confirm
    from scripts.e2e.vm import VM
    session_id(session)
    require(session != "b616980ef1c99192", "e2e-existing-builder-requires-specific-closure")
    # Evidence is bounded private JSON; no arbitrary comments or secrets persisted.
    from scripts.e2e.staging import source
    with source(evidence_path, private=True) as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, "e2e-closure-evidence-too-large")
    proof = parse_snapshot(raw)
    fields = {"session", "receiptSha256", "controllersStopped", "receiptsReviewed",
              "noUnfinishedMultipassOperations", "vmInventoryIndependentlyChecked"}
    require(isinstance(proof, dict) and set(proof) == fields and proof["session"] == session and
            all(proof[key] is True for key in fields - {"session", "receiptSha256"}), "e2e-closure-evidence-invalid")
    with Store(root()).locked() as control, Store(path).locked() as store:
        state = store.read("execution.json")
        require(isinstance(state, dict) and state.get("session") == session and
                state.get("phase") == "builder-prepare" and state.get("status") == "unknown" and
                state.get("identities") == {} and state.get("references") == {} and "build" not in state,
                "e2e-closure-only-absent-builder-prepare")
        require(proof["receiptSha256"] == digest(state), "e2e-closure-receipt-changed")
        require(not any(name.endswith(".pending") or name in ("images.tar", "artifacts", "exported", "raw")
                        for name in os.listdir(store.fd)), "e2e-closure-unfinished-artifacts")
        expected = (f"pd-fixture-builder-{session}", f"pd-fixture-publisher-{session}", f"pd-e2e-{session}")
        require((state["builder"], state["publisher"]) == expected[:2], "e2e-closure-identity-mismatch")
        require(control.read("closed-" + session + ".json") is None, "e2e-closure-already-recorded")
        machine = (factory or VM)(session)
        def absent_inventory():
            value = inventory(machine)
            require(not any(item["name"] in expected for item in value["list"]), "e2e-closure-vm-still-present")
            return digest(value)
        first = absent_inventory()
        (consent or confirm)(f"Phase: close-unknown; session: {session}; VM: {state['builder']} (absent).\n"
                            "Record operator decision: builder preparation did not create a VM. Preserve original unknown receipt, "
                            "all evidence and locks. No retry, resume or resource deletion. A new session needs separate approval.",
                            f"close-unknown:{session}")
        require(store.read("execution.json") == state, "e2e-closure-receipt-changed")
        second = absent_inventory()
        decision = {"version": 1, "session": session, "directory": str(store.path),
                    "receiptSha256": digest(state), "evidence": proof, "evidenceSha256": sha256(raw).hexdigest(),
                    "inventoryBeforeSha256": first, "inventoryAfterSha256": second,
                    "decision": "closed-absent-builder-no-replay", "operatorUid": os.geteuid(),
                    "at": datetime.now(timezone.utc).isoformat()}
        control.write("closed-" + session + ".json", decision)
        return {"status": "passed", "decision": decision, "originalReceipt": "preserved-unknown"}
