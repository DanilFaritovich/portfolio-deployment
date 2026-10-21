"""Offline global fence and evidence-based unknown closure, without VM operations."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from scripts.docker_adapter import DockerError
from scripts.deployment_journal import JournalError
from scripts.e2e import control
from scripts.e2e.host_state import Store

SESSION = "4d51ffc5eb90ba5a"


def setup_unknown(tmp_path):
    path = Path.home() / ".local/share/portfolio-deployment/e2e-fixtures"
    state = {"version": 1, "session": SESSION, "builder": f"pd-fixture-builder-{SESSION}",
             "publisher": f"pd-fixture-publisher-{SESSION}", "phase": "builder-prepare", "status": "unknown",
             "identities": {}, "references": {}, "reason": "docker-command-failed"}
    with Store(path).locked() as store:
        store.write("execution.json", state)
    evidence = tmp_path / "independent-review.json"
    proof = {"session": SESSION, "receiptSha256": control.digest(state), "controllersStopped": True,
             "receiptsReviewed": True, "noUnfinishedMultipassOperations": True,
             "vmInventoryIndependentlyChecked": True}
    evidence.write_text(json.dumps(proof))
    evidence.chmod(0o600)
    return path, state, evidence


class Inventory:
    calls = []
    records = []

    def __init__(self, session):
        assert session == SESSION

    def command(self, *args):
        assert args == ("list", "--format", "json")
        self.calls.append(args)
        return json.dumps({"list": self.records}).encode()


@pytest.fixture(autouse=True)
def reset_inventory():
    Inventory.calls = []
    Inventory.records = []


def test_unknown_cannot_be_bypassed_by_new_directory(tmp_path):
    path, state, _ = setup_unknown(tmp_path)
    lock = (path / "controller.lock").stat().st_ino
    with pytest.raises(DockerError, match="global-unfinished"):
        with control.guard(tmp_path / "new-output", "fixtures"):
            pytest.fail("unknown bypassed")
    with Store(path).locked() as store:
        assert store.read("execution.json") == state
    assert (path / "controller.lock").stat().st_ino == lock
    assert not (tmp_path / "new-output").exists()


def test_custom_unknown_tracked_across_output_changes(tmp_path):
    custom = tmp_path / "custom"
    with control.guard(custom, "fixtures"):
        with Store(custom).locked() as store:
            store.write("execution.json", {"session": SESSION, "status": "unknown", "phase": "build"})
    with pytest.raises(DockerError, match="global-unfinished"):
        with control.guard(tmp_path / "other", "state"):
            pytest.fail("custom unknown bypassed")
    with Store(control.root()).locked() as store:
        assert store.read("active.json")["directory"] == str(custom)


def test_fixed_global_lock_blocks_parallel_operations(tmp_path):
    with control.guard(tmp_path / "first", "fixtures"):
        with pytest.raises(DockerError, match="controller-busy"):
            with control.guard(tmp_path / "second", "state"):
                pytest.fail("parallel controller")


def test_diagnosis_reports_phase_receipt_and_only_readonly_inventory(tmp_path):
    path, state, _ = setup_unknown(tmp_path)
    report = control.diagnose(path, SESSION, factory=Inventory)
    assert report["readOnly"] is True and report["phase"] == "builder-prepare" and report["vms"] == []
    assert report["receipt"] == state and report["receiptSha256"] == control.digest(state)
    assert len(Inventory.calls) == 1


def test_close_unknown_preserves_original_and_retains_decision_evidence(tmp_path):
    path, state, evidence = setup_unknown(tmp_path)
    original = (path / "execution.json").read_bytes()
    inode = (path / "controller.lock").stat().st_ino
    confirmations = []
    result = control.close_unknown(path, SESSION, evidence, factory=Inventory,
                                   consent=lambda *args: confirmations.append(args))
    assert result["status"] == "passed" and result["originalReceipt"] == "preserved-unknown"
    assert len(Inventory.calls) == 2 and len(confirmations) == 1
    assert SESSION in confirmations[0][0] and state["builder"] in confirmations[0][0]
    assert (path / "execution.json").read_bytes() == original
    assert (path / "controller.lock").stat().st_ino == inode
    with Store(control.root()).locked() as store:
        decision = store.read("closed-" + SESSION + ".json")
        assert decision["evidence"]["noUnfinishedMultipassOperations"] is True
        assert decision["receiptSha256"] == control.digest(state)
    # No replay: selecting the same original bundle is still blocked by execute;
    # only the global fence permits a separately authorized new session now.
    with control.guard(tmp_path / "new-reviewed-output", "fixtures"):
        pass
    with pytest.raises(DockerError, match="already-recorded"):
        control.close_unknown(path, SESSION, evidence, factory=Inventory, consent=lambda *a: None)


@pytest.mark.parametrize("field", ["controllersStopped", "receiptsReviewed", "noUnfinishedMultipassOperations",
                                   "vmInventoryIndependentlyChecked", "receiptSha256", "session"])
def test_close_requires_all_independent_evidence(tmp_path, field):
    path, _, evidence = setup_unknown(tmp_path)
    proof = json.loads(evidence.read_text())
    proof[field] = False
    evidence.write_text(json.dumps(proof))
    with pytest.raises(DockerError):
        control.close_unknown(path, SESSION, evidence, factory=Inventory, consent=lambda *a: pytest.fail("confirmation"))
    assert not Inventory.calls


@pytest.mark.parametrize("change", ["vm", "phase", "identity", "build", "pending", "artifacts", "symlink"])
def test_close_blocks_unsafe_or_advanced_state(tmp_path, change):
    path, state, evidence = setup_unknown(tmp_path)
    if change == "vm":
        Inventory.records = [{"name": state["builder"], "state": "Starting"}]
    elif change in ("pending", "artifacts"):
        (path / ("execution.json.pending" if change == "pending" else "images.tar")).touch(mode=0o600)
    elif change == "symlink":
        other = path / "other.json"
        (path / "execution.json").rename(other)
        (path / "execution.json").symlink_to(other)
    else:
        changed = deepcopy(state)
        if change == "phase":
            changed["phase"] = "build"
        elif change == "identity":
            changed["identities"] = {"builder": "machine-id"}
        else:
            changed["build"] = {"archive": "a" * 64}
        with Store(path).locked() as store:
            store.write("execution.json", changed)
    with pytest.raises((DockerError, OSError, JournalError)):
        control.close_unknown(path, SESSION, evidence, factory=Inventory, consent=lambda *a: pytest.fail("confirmation"))
    with Store(control.root()).locked() as store:
        assert store.read("closed-" + SESSION + ".json") is None


def test_close_decline_or_inventory_change_never_records_decision(tmp_path):
    path, _, evidence = setup_unknown(tmp_path)
    def decline(*args):
        raise DockerError("e2e-operation-declined")
    with pytest.raises(DockerError, match="declined"):
        control.close_unknown(path, SESSION, evidence, factory=Inventory, consent=decline)
    def changed(*args):
        Inventory.records = [{"name": f"pd-e2e-{SESSION}", "state": "Starting"}]
    with pytest.raises(DockerError, match="vm-still-present"):
        control.close_unknown(path, SESSION, evidence, factory=Inventory, consent=changed)
    with Store(control.root()).locked() as store:
        assert store.read("closed-" + SESSION + ".json") is None
