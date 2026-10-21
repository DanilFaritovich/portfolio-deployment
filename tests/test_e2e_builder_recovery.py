"""Incident recovery with fake Multipass only; no host receipts or live guests."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import builder_recovery as recovery, builder_review, cli, control, fixture_execution
from scripts.e2e.host_state import Store


class Machine:
    state = "Running"
    records = None
    metadata = {"image_hash": "a" * 64, "cpu_count": 2, "release": "24.04"}
    identity = None
    calls = []

    def __init__(self, session, kind):
        assert session == recovery.SESSION and kind == "builder"
        self.name = "pd-fixture-builder-" + session

    def command(self, *args):
        assert args == ("list", "--format", "json")
        self.calls.append("list")
        return json.dumps({"list": self.records if self.records is not None else
                           [{"name": self.name, "state": self.state}]}).encode()

    def info(self):
        self.calls.append("info")
        return {"state": self.state, "mounts": {}, **self.metadata}

    def checked_exec(self, context, *args, **kwargs):
        assert context == "identity" and args[:2] == ("python3", "-c")
        assert "review('" + recovery.SESSION + "')" in args[2]
        assert kwargs == {"timeout": 30}
        self.calls.append("read-only-guest-review")
        return json.dumps(self.identity).encode()


@pytest.fixture
def incident(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("real subprocess/network forbidden")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    path = recovery.incident_path()
    state = {"version": 1, "session": recovery.SESSION, "phase": "builder-prepare", "status": "unknown",
             "builder": "pd-fixture-builder-" + recovery.SESSION,
             "publisher": "pd-fixture-publisher-" + recovery.SESSION,
             "identities": {}, "references": {}, "reason": "multipass-cloud-init-failed"}
    # Synthetic receipt cannot have the real operator-provided digest. Production
    # keeps the incident pin; only this isolated fixture substitutes its digest.
    monkeypatch.setattr(recovery, "RECEIPT_SHA256", control.digest(state))
    with Store(path).locked() as store:
        store.write("execution.json", state)
        (path / "builder-cloud-init.yaml").write_text("#cloud-config\nruncmd: []\n")
        (path / "builder-cloud-init.yaml").chmod(0o600)
        (path / "context").mkdir(mode=0o700)
        for name in ("Dockerfile", "app.py"):
            target = path / "context" / name
            target.write_text("synthetic")
            target.chmod(0o600)
    with Store(control.root()).locked() as journal:
        journal.write("active.json", {"directory": str(path), "kind": "fixtures"})
    Machine.state, Machine.records, Machine.calls = "Running", None, []
    Machine.metadata = {"image_hash": "a" * 64, "cpu_count": 2, "release": "24.04"}
    Machine.identity = {"name": state["builder"], "hostname": state["builder"], "session": recovery.SESSION,
                        "kind": "builder", "machineId": "b" * 32,
                        "marker": {"session": recovery.SESSION, "kind": "builder", "machine": "b" * 32},
                        "noGuestContinuation": True}
    return path, state


def reviewed(incident, tmp_path):
    path, state = incident
    result = recovery.collect(path, recovery.SESSION, factory=Machine)
    proof = {"session": recovery.SESSION, "receiptSha256": control.digest(state),
             "reviewSha256": result["reviewSha256"], "identity": deepcopy(Machine.identity),
             **dict.fromkeys(recovery.ATTESTATIONS, True)}
    evidence = tmp_path / "operator-evidence.json"
    evidence.write_text(json.dumps(proof))
    evidence.chmod(0o600)
    return evidence


def assert_blocked(path):
    with Store(control.root()).locked() as journal:
        assert journal.read("closed-" + recovery.SESSION + ".json") is None
    with pytest.raises(DockerError, match="global-unfinished"):
        with control.guard(path.parent / "new-output", "fixtures"):
            pytest.fail("unknown bypassed")


def test_stopped_closure_preserves_every_input_and_forbids_replay(incident, tmp_path, monkeypatch):
    path, state = incident
    original = {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    inodes = [(p, p.stat().st_ino) for p in (path / "controller.lock", control.root() / "controller.lock")]
    evidence = reviewed(incident, tmp_path)
    assert {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()} == original
    Machine.state = "Stopped"
    Machine.metadata["release"] = None  # Guest-derived release is unavailable after stop.
    Machine.calls = []
    approvals = []
    result = recovery.close(path, recovery.SESSION, evidence, factory=Machine,
                            consent=lambda *args: approvals.append(args))
    assert result["originalReceipt"] == "preserved-unknown" and len(approvals) == 1
    assert "Stopped" in approvals[0][0] and approvals[0][1] == "close-failed-builder:" + recovery.SESSION
    assert Machine.calls == ["list", "info", "list", "info"]  # Never exec/start/stop/delete after stop.
    assert {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()} == original
    assert all(p.stat().st_ino == inode for p, inode in inodes)
    decision_path = control.root() / ("closed-" + recovery.SESSION + ".json")
    decision = decision_path.read_bytes()
    with pytest.raises(DockerError, match="already-recorded"):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: pytest.fail("repeat prompt"))
    assert decision_path.read_bytes() == decision
    with pytest.raises(DockerError, match="closed-session-no-replay"):
        with control.guard(path, "fixtures"):
            pytest.fail("old directory replay")
    with control.guard(path.parent / "fresh-output", "fixtures"):
        pass
    # Even a generated session-ID collision cannot reuse a closed session.
    monkeypatch.setattr(fixture_execution.secrets, "token_hex", lambda _: recovery.SESSION)
    with pytest.raises(DockerError, match="closed-session-no-replay"):
        fixture_execution.execute(path.parent / "fresh-execute", "fixtures",
                                  "docker.io/library/python@sha256:" + "a" * 64,
                                  factory=lambda *a: pytest.fail("VM created for retired session"))


@pytest.mark.parametrize("state", ["Running", "Deleted", "Starting", "Unknown", "absent"])
def test_close_requires_existing_stopped_builder(incident, tmp_path, state):
    path, _ = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = state
    if state == "absent":
        Machine.records = []
    with pytest.raises(DockerError, match="vm-must-be-stopped"):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: pytest.fail("confirmation"))
    assert_blocked(path)


@pytest.mark.parametrize("field", ["name", "hostname", "session", "kind", "machineId", "marker", "noGuestContinuation"])
def test_collect_rejects_wrong_identity_or_guest_progress(incident, field):
    path, _ = incident
    Machine.identity[field] = False
    with pytest.raises(DockerError, match="identity-invalid"):
        recovery.collect(path, recovery.SESSION, factory=Machine)
    assert_blocked(path)


@pytest.mark.parametrize("field", sorted(recovery.ATTESTATIONS) + ["receiptSha256", "reviewSha256", "identity"])
def test_close_requires_independent_evidence(incident, tmp_path, field):
    path, _ = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = "Stopped"
    proof = json.loads(evidence.read_text())
    proof[field] = False
    evidence.write_text(json.dumps(proof))
    with pytest.raises(DockerError):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: pytest.fail("confirmation"))
    assert_blocked(path)


def test_missing_evidence_and_missing_review_fail_closed(incident, tmp_path):
    path, _ = incident
    with pytest.raises(DockerError):
        recovery.close(path, recovery.SESSION, tmp_path / "missing.json", factory=Machine)
    evidence = tmp_path / "unreviewed.json"
    evidence.write_text(json.dumps({**dict.fromkeys(recovery.ATTESTATIONS, True), "session": recovery.SESSION,
                                   "receiptSha256": recovery.RECEIPT_SHA256, "reviewSha256": "a" * 64,
                                   "identity": Machine.identity}))
    evidence.chmod(0o600)
    Machine.state = "Stopped"
    with pytest.raises(DockerError, match="evidence-mismatch"):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine,
                       consent=lambda *a: pytest.fail("confirmation without review"))
    assert_blocked(path)


@pytest.mark.parametrize("change", ["receipt", "pending", "nested-pending", "images", "publication", "source", "control-pending"])
def test_progress_or_changed_inputs_block_closure(incident, tmp_path, change):
    path, state = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = "Stopped"
    if change == "receipt":
        with Store(path).locked() as store:
            store.write("execution.json", {**state, "status": "passed"})
    elif change == "source":
        (path / "context/app.py").write_text("changed")
    else:
        target = {"pending": path / "execution.json.pending", "nested-pending": path / "context/build.pending",
                  "images": path / "images.tar", "publication": path / "raw", "control-pending": control.root() / "closed-test.pending"}[change]
        target.touch(mode=0o600)
    with pytest.raises(DockerError):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: pytest.fail("confirmation"))
    with Store(control.root()).locked() as journal:
        assert journal.read("closed-" + recovery.SESSION + ".json") is None
    with pytest.raises(DockerError):
        control.preflight()


@pytest.mark.parametrize("change", ["decline", "receipt", "evidence", "source", "running", "metadata", "review"])
def test_prompt_boundary_rechecks_and_operator_decline(incident, tmp_path, change):
    path, _ = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = "Stopped"
    def consent(*args):
        if change == "decline":
            raise DockerError("e2e-operation-declined")
        if change == "receipt":
            (path / "execution.json").write_text("{}")
        elif change == "evidence":
            evidence.write_text("{}")
        elif change == "source":
            (path / "context/app.py").write_text("changed")
        elif change == "running":
            Machine.state = "Running"
        elif change == "metadata":
            Machine.metadata["image_hash"] = "c" * 64
        elif change == "review":
            (control.root() / ("builder-review-" + recovery.SESSION + ".json")).write_text("{}")
    with pytest.raises(DockerError):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=consent)
    assert_blocked(path)


def test_changed_retained_receipt_blocks_new_work_after_closure(incident, tmp_path):
    path, _ = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = "Stopped"
    recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: None)
    with control.guard(path.parent / "new-output", "fixtures"):
        pass
    (path / "execution.json").write_text("{}")
    with pytest.raises(DockerError, match="receipt-changed"):
        control.preflight()


def test_journal_immutable_write_never_overwrites(incident):
    with Store(control.root()).locked() as journal:
        journal.write("test.json", {"original": True}, immutable=True)
        with pytest.raises(FileExistsError):
            journal.write("test.json", {"replacement": True}, immutable=True)
        assert journal.read("test.json") == {"original": True}
        assert (control.root() / "test.json.pending").exists()
    with pytest.raises(DockerError, match="control-pending"):
        control.preflight()


@pytest.mark.parametrize("state", ["Stopped", "Deleted", "absent"])
def test_collect_requires_running_original_vm(incident, state):
    path, _ = incident
    Machine.state = state
    if state == "absent":
        Machine.records = []
    with pytest.raises(DockerError, match="vm-must-be-running"):
        recovery.collect(path, recovery.SESSION, factory=Machine)
    assert "read-only-guest-review" not in Machine.calls


@pytest.mark.parametrize("problem", ["mounts", "publisher", "changed-metadata", "lost-review"])
def test_uncertain_vm_or_review_retention_blocks(incident, tmp_path, monkeypatch, problem):
    path, state = incident
    evidence = reviewed(incident, tmp_path)
    Machine.state = "Stopped"
    if problem == "mounts":
        monkeypatch.setattr(Machine, "info", lambda self: {"state": "Stopped", "mounts": {"host": {}}, **Machine.metadata})
    elif problem == "publisher":
        Machine.records = [{"name": state["builder"], "state": "Stopped"},
                           {"name": state["publisher"], "state": "Stopped"}]
    elif problem == "changed-metadata":
        Machine.metadata["image_hash"] = "d" * 64
    else:
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: None)
        (control.root() / ("builder-review-" + recovery.SESSION + ".json")).unlink()
        with pytest.raises(DockerError, match="evidence-changed"):
            control.preflight()
        return
    with pytest.raises(DockerError):
        recovery.close(path, recovery.SESSION, evidence, factory=Machine, consent=lambda *a: pytest.fail("confirmation"))
    assert_blocked(path)


@pytest.mark.parametrize("problem", [None, "marker", "hostname", "machine", "context", "storage", "process", "payload", "symlink"])
def test_guest_inspector_checks_actual_files_and_processes(tmp_path, problem):
    root, processes = tmp_path / "guest", tmp_path / "proc"
    (root / "etc").mkdir(parents=True)
    (root / "var/lib/pd-fixtures/context").mkdir(parents=True)
    processes.mkdir()
    marker = {"session": recovery.SESSION, "kind": "builder", "machine": "b" * 32}
    (root / "etc/pd-fixture.json").write_text(json.dumps(marker))
    (root / "etc/pd-fixture.json").chmod(0o600)
    (root / "etc/machine-id").write_text("b" * 32)
    (root / "etc/hostname").write_text("pd-fixture-builder-" + recovery.SESSION)
    if problem in ("marker", "hostname", "machine"):
        target = {"marker": "pd-fixture.json", "hostname": "hostname", "machine": "machine-id"}[problem]
        (root / "etc" / target).write_text("{}")
    elif problem == "context":
        (root / "var/lib/pd-fixtures/context/Dockerfile").touch()
    elif problem == "storage":
        (root / "var/lib/containers/storage").mkdir(parents=True)
        (root / "var/lib/containers/storage/build-result").touch()
    elif problem == "process":
        (processes / "999999").mkdir()
        (processes / "999999/cmdline").write_bytes(b"python3\0/var/lib/pd-fixtures/fixture_guest.py\0build\0")
    elif problem == "payload":
        (root / "var/lib/pd-fixtures/fixture_guest.py").touch()
    elif problem == "symlink":
        (root / "etc/machine-id").rename(root / "etc/real-machine-id")
        (root / "etc/machine-id").symlink_to(root / "etc/real-machine-id")
    if problem is None:
        result = builder_review.review(recovery.SESSION, root, processes, owner=os.geteuid())
        assert result["marker"] == marker and result["noGuestContinuation"] is True
    else:
        with pytest.raises((ValueError, OSError)):
            builder_review.review(recovery.SESSION, root, processes, owner=os.geteuid())


def test_cli_routes_both_recovery_commands(incident, monkeypatch, capsys):
    path, _ = incident
    for action, function in (("review-failed-builder", "collect"), ("close-failed-builder", "close")):
        calls = []
        monkeypatch.setattr(recovery, function, lambda *args: calls.append(args) or {"status": "passed"})
        assert cli.main([action, "--output", str(path), "--session", recovery.SESSION, "--evidence", "proof.json"]) == 0
        assert calls[0][:2] == (path, recovery.SESSION)
        capsys.readouterr()
