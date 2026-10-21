"""Incident-specific, evidence-bound closure; VM stop/disposal is operator-owned."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from scripts.e2e import control
from scripts.e2e.host_state import Store
from scripts.e2e.safety import require
from scripts.e2e.staging import source
from scripts.planning_inputs import parse_snapshot

SESSION = "b616980ef1c99192"
RECEIPT_SHA256 = "55ee11e0476d3026839f2decb989238e57c1fe21a54191b27cbf6b18d01f77a1"
ATTESTATIONS = {"controllersStopped", "receiptsReviewed", "noUnfinishedMultipassOperations",
                "noBuildOrPublication", "vmIdentityIndependentlyChecked", "noVmReplacementSinceReview",
                "evidencePreserved", "vmStoppedByOperatorAfterReview", "cloudInitSchemaFailureReviewed"}
INCIDENT_EVIDENCE_FILES = {"SHA256SUMS", "builder-cloud-init.yaml", "cloud-init-status.txt", "execution.json",
                           "hostname.txt", "machine-id.txt", "multipass-info.json", "multipass-list.json", "vm-marker.json"}


def retained_incident_evidence(store):
    # Earlier operator diagnostics already exist here. Retain and hash only this
    # exact flat evidence bundle, without adopting any of its claims as authority.
    if "incident-evidence" not in os.listdir(store.fd):
        return {}
    fd = os.open("incident-evidence", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store.fd)
    try:
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o077, "unsafe-e2e-host-state")
        require(set(os.listdir(fd)) == INCIDENT_EVIDENCE_FILES, "e2e-closure-unfinished-artifacts")
        hashes = {}
        for name in sorted(INCIDENT_EVIDENCE_FILES):
            with source(store.path / "incident-evidence" / name, private=True) as stream:
                raw = stream.read(65537)
                require(len(raw) <= 65536, "e2e-builder-closure-input-too-large")
                hashes["incident-evidence/" + name] = sha256(raw).hexdigest()
        return hashes
    finally:
        os.close(fd)


def incident_path():
    return Path.home() / ".local/share/portfolio-deployment/e2e-fixtures-attempt-2"


def inputs(store, session):
    require(session == SESSION and store.path == incident_path(), "e2e-builder-closure-incident-mismatch")
    state = store.read("execution.json")
    require(isinstance(state, dict) and control.digest(state) == RECEIPT_SHA256,
            "e2e-closure-receipt-changed")
    require(state.get("session") == session and state.get("phase") == "builder-prepare" and
            state.get("status") == "unknown" and state.get("identities") == {} and state.get("references") == {} and
            "build" not in state and state.get("reason") == "multipass-cloud-init-failed" and
            state.get("builder") == f"pd-fixture-builder-{session}" and
            state.get("publisher") == f"pd-fixture-publisher-{session}", "e2e-builder-closure-phase-invalid")
    # Bound the entire original directory, refuse links/unknown files and pin all
    # bytes. New operator evidence belongs in a separate directory. The exact
    # pre-existing incident-evidence bundle remains in place as retained input.
    allowed = {"controller.lock", "execution.json", "plan.json", "repositories.json", "python-base.json",
               "fixture_guest.py", "builder-cloud-init.yaml", "context", "incident-evidence"}
    names = set(os.listdir(store.fd))
    require(names <= allowed and {"execution.json", "builder-cloud-init.yaml", "context"} <= names,
            "e2e-closure-unfinished-artifacts")
    hashes = retained_incident_evidence(store)
    for name in sorted(names - {"controller.lock", "context", "incident-evidence"}):
        if name.endswith(".json"):
            store.read(name)
        with source(store.path / name, private=True) as stream:
            raw = stream.read(65537)
            require(len(raw) <= 65536, "e2e-builder-closure-input-too-large")
        hashes[name] = sha256(raw).hexdigest()
    context_fd = os.open("context", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store.fd)
    try:
        require(set(os.listdir(context_fd)) == {"Dockerfile", "app.py"}, "e2e-closure-unfinished-artifacts")
        for name in ("Dockerfile", "app.py"):
            with source(store.path / "context" / name, private=True) as stream:
                raw = stream.read(65537)
                require(len(raw) <= 65536, "e2e-builder-closure-input-too-large")
                hashes["context/" + name] = sha256(raw).hexdigest()
    finally:
        os.close(context_fd)
    return state, hashes


def snapshot(machine, state, required_state):
    listing = control.inventory(machine)
    expected = {state["builder"], state["publisher"], "pd-e2e-" + SESSION}
    matches = [item for item in listing["list"] if item["name"] in expected]
    require(len(matches) == 1 and matches[0]["name"] == state["builder"] and
            matches[0].get("state") == required_state, "e2e-builder-closure-vm-must-be-" + required_state.lower())
    info = machine.info()
    require(info.get("state") == required_state and not info.get("mounts") and
            isinstance(info.get("image_hash"), str) and re.fullmatch(r"[0-9a-f]{64}", info["image_hash"]),
            "e2e-builder-closure-vm-info-invalid")
    # Multipass has no guest machine-id in stopped metadata. The running guest
    # review and independent operator no-replacement attestation bridge the stop.
    # Guest-reported release/load/IP/usage can disappear when stopped. Multipass
    # also leaves cpu_count empty then; corroborate it with the saved setting,
    # without starting or executing inside the stopped guest.
    stable = {key: info.get(key) for key in ("image_hash", "image_release", "cpu_count")}
    cpus = stable["cpu_count"]
    if cpus in (None, ""):
        cpus = machine.command("get", "local." + state["builder"] + ".cpus").decode().strip()
    require(re.fullmatch(r"[1-9][0-9]*", str(cpus)) is not None,
            "e2e-builder-closure-cpu-count-unverified")
    stable["cpu_count"] = str(cpus)
    return {"inventorySha256": control.digest(listing), "metadata": stable}


def machine_for(session, factory):
    from scripts.e2e.fixture_execution import FixtureVM
    return (factory or FixtureVM)(session, "builder")


def no_pending(control_store):
    require(not any(name.endswith(".pending") for name in os.listdir(control_store.fd)),
            "e2e-control-pending-review-required")


def tracked(journal, store):
    require(journal.read("active.json") == {"directory": str(store.path), "kind": "fixtures"},
            "e2e-builder-closure-tracked-directory-mismatch")


def collect(path, session, *, factory=None):
    """Explicit read-only guest inspection plus immutable local review; never stop."""
    with Store(control.root()).locked() as journal, Store(path).locked() as store:
        no_pending(journal)
        tracked(journal, store)
        state, hashes = inputs(store, session)
        name = "builder-review-" + session + ".json"
        require(journal.read(name) is None and journal.read("closed-" + session + ".json") is None,
                "e2e-builder-closure-already-reviewed")
        machine = machine_for(session, factory)
        first = snapshot(machine, state, "Running")
        payload = Path(__file__).with_name("builder_review.py").read_text()
        # Bypass normal adoption solely for this fixed read-only inspector. The
        # inspector itself checks marker ownership/session/kind/machine/hostname.
        identity = parse_snapshot(machine.checked_exec("identity", "python3", "-c",
                                  payload + f"\nprint(json.dumps(review('{session}')))\n", timeout=30))
        require(isinstance(identity, dict) and set(identity) ==
                {"name", "hostname", "session", "kind", "machineId", "marker", "noGuestContinuation"} and
                identity["name"] == state["builder"] and identity["hostname"] == state["builder"] and
                identity["session"] == session and identity["kind"] == "builder" and
                isinstance(identity["machineId"], str) and re.fullmatch(r"[0-9a-f]{32}", identity["machineId"]) and
                identity["marker"] == {"session": session, "kind": "builder", "machine": identity["machineId"]} and
                identity["noGuestContinuation"] is True, "e2e-builder-closure-identity-invalid")
        second = snapshot(machine, state, "Running")
        require(first == second and inputs(store, session)[1] == hashes, "e2e-builder-closure-review-changed")
        record = {"version": 1, "session": session, "directory": str(store.path), "receiptSha256": RECEIPT_SHA256,
                  "inputs": hashes, "identity": identity, "snapshot": second,
                  "at": datetime.now(timezone.utc).isoformat(), "operatorUid": os.geteuid()}
        journal.write(name, record, immutable=True)
        return {"status": "passed", "review": record, "reviewSha256": control.digest(record),
                "next": "independent-evidence-review-then-separate-operator-stop; not-closed"}


def evidence(path):
    with source(path, private=True) as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, "e2e-closure-evidence-too-large")
    return parse_snapshot(raw), sha256(raw).hexdigest()


def close(path, session, evidence_path, *, factory=None, consent=None):
    from scripts.e2e.orchestration import confirm
    proof, evidence_hash = evidence(evidence_path)
    require(isinstance(proof, dict) and set(proof) == ATTESTATIONS | {"session", "receiptSha256", "reviewSha256", "identity"} and
            all(proof[key] is True for key in ATTESTATIONS), "e2e-closure-evidence-invalid")
    with Store(control.root()).locked() as journal, Store(path).locked() as store:
        no_pending(journal)
        tracked(journal, store)
        state, hashes = inputs(store, session)
        review_name = "builder-review-" + session + ".json"
        reviewed = journal.read(review_name)
        require(reviewed is not None and proof["session"] == session and proof["receiptSha256"] == RECEIPT_SHA256 and
                proof["reviewSha256"] == control.digest(reviewed) and proof["identity"] == reviewed["identity"] and
                reviewed["inputs"] == hashes and reviewed["directory"] == str(store.path),
                "e2e-builder-closure-evidence-mismatch")
        decision_name = "closed-" + session + ".json"
        require(journal.read(decision_name) is None, "e2e-closure-already-recorded")
        machine = machine_for(session, factory)
        first = snapshot(machine, state, "Stopped")
        require(first["metadata"] == reviewed["snapshot"]["metadata"], "e2e-builder-closure-vm-changed")
        (consent or confirm)(f"Phase: close-failed-builder; session: {session}; VM: {state['builder']} (Stopped).\n"
                            "Record failed builder preparation closure. Preserve unknown receipt, VM, staging, locks and evidence. "
                            "No retry, resume, start, stop or deletion. New session requires separate approval.",
                            "close-failed-builder:" + session)
        no_pending(journal)
        tracked(journal, store)
        require(inputs(store, session)[1] == hashes and journal.read(review_name) == reviewed and
                evidence(evidence_path) == (proof, evidence_hash), "e2e-builder-closure-evidence-changed")
        second = snapshot(machine, state, "Stopped")
        require(first == second, "e2e-builder-closure-vm-changed")
        decision = {"version": 1, "session": session, "directory": str(store.path), "receiptSha256": RECEIPT_SHA256,
                    "inputs": hashes, "identity": reviewed["identity"], "reviewSha256": control.digest(reviewed),
                    "evidence": proof, "evidenceSha256": evidence_hash, "stoppedSnapshot": second,
                    "decision": "closed-stopped-failed-builder-no-replay", "operatorUid": os.geteuid(),
                    "at": datetime.now(timezone.utc).isoformat()}
        journal.write(decision_name, decision, immutable=True)
        return {"status": "passed", "decision": decision, "originalReceipt": "preserved-unknown"}


def verify_retained(decision, journal):
    reviewed = journal.read("builder-review-" + decision["session"] + ".json")
    require(reviewed is not None and control.digest(reviewed) == decision["reviewSha256"],
            "e2e-builder-closure-evidence-changed")
    require(os.path.isdir(decision["directory"]), "e2e-tracked-evidence-missing")
    with Store(decision["directory"]).locked() as store:
        _, hashes = inputs(store, decision["session"])
        require(hashes == decision["inputs"], "e2e-builder-closure-evidence-changed")
