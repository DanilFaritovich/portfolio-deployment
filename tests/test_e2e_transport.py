"""Exercise transport boundaries offline with synthetic subprocesses and guests."""
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import multipass, staging, vm, fixture_execution

SESSION = "a" * 16


def test_bounded_runner_retains_only_safe_stderr():
    with pytest.raises(multipass.MultipassError) as caught:
        multipass.run([sys.executable, "-c", "import sys; sys.stderr.write('Permission denied: ghp_SENTINEL https://user:password@host'); sys.exit(1)"],
                      operation="launch")
    assert caught.value.code == "multipass-launch-failed"
    assert caught.value.evidence == {"operation": "launch", "stderr": "permission denied"}


@pytest.mark.parametrize("mode", ["timeout", "output-limit"])
def test_bounded_runner_uncertainty(mode, monkeypatch):
    monkeypatch.setattr(multipass, "MAX_OUTPUT", 32)
    code = "import time; time.sleep(2)" if mode == "timeout" else "print('x'*1000)"
    with pytest.raises(multipass.MultipassError) as caught:
        multipass.run([sys.executable, "-c", code], timeout=0.05, operation="transfer")
    assert caught.value.code == f"multipass-transfer-{mode}" and caught.value.unknown


@pytest.mark.parametrize("operation", ["list", "launch", "exec", "transfer"])
def test_actual_vm_boundary_classifies_injected_docker_error(operation):
    def fail(*args, **kwargs):
        raise DockerError("docker-command-failed", True)
    machine = vm.VM(SESSION, run=fail)
    with pytest.raises(multipass.MultipassError, match=f"multipass-{operation}-failed"):
        machine.command(operation)


@pytest.mark.parametrize("context", ["identity", "cloud-init"])
def test_guest_context_preserves_safe_evidence(context):
    def fail(*args, **kwargs):
        raise multipass.MultipassError("exec", "failed", True, b"permission denied private-sentinel")
    machine = vm.VM(SESSION, run=fail)
    with pytest.raises(multipass.MultipassError) as caught:
        machine.checked_exec(context, "true")
    assert caught.value.code == f"multipass-{context}-failed"
    assert caught.value.evidence["stderr"] == "permission denied"


def test_snap_probe_uses_confined_shell_and_checks_read_write_hash(monkeypatch):
    monkeypatch.setattr(staging.shutil, "which", lambda _: "/usr/bin/snap")
    path = Path.home() / "PortfolioDeploymentE2E" / "file with 'quotes'"
    expected = {"size": 17, "sha256": "b" * 64}
    def run(argv, **kwargs):
        assert argv == ["/usr/bin/snap", "run", "--shell", "multipass"]
        script = kwargs["input_data"].decode()
        assert "test -r " in script and ": >> " in script and "wc -c < " in script and "sha256sum -- " in script
        return ("17\n" + "b" * 64 + "  file\n").encode()
    monkeypatch.setattr(staging, "run", run)
    staging.snap_probe(path, expected)
    def denied(*args, **kwargs):
        raise multipass.MultipassError("staging-access", "failed", stderr=b"permission denied SECRET")
    monkeypatch.setattr(staging, "run", denied)
    with pytest.raises(multipass.MultipassError, match="denied-check-snap-home-permission"):
        staging.snap_probe(path, expected)


@pytest.mark.parametrize("role", ["e2e", "builder", "publisher"])
def test_upload_verifies_guest_before_and_after_transfer(tmp_path, monkeypatch, role):
    probes, events = [], []
    monkeypatch.setattr(staging, "snap_probe", lambda p, d: probes.append((p, d)))
    machine = vm.VM(SESSION) if role == "e2e" else fixture_execution.FixtureVM(SESSION, role)
    payload = tmp_path / "plan.json"
    payload.write_bytes(b"synthetic non-secret plan")
    payload.chmod(0o600)
    def guest(*args, **kwargs):
        events.append(("guest", args))
        return b""
    def command(*args, **kwargs):
        events.append(("command", args))
        assert args[0] == "transfer"
        staged = Path(args[1])
        assert staged != payload and staged.is_relative_to(Path.home() / "PortfolioDeploymentE2E" / SESSION)
        assert staged.read_bytes() == payload.read_bytes()
        assert staged.stat().st_mode & 0o777 == 0o600
        return b""
    machine.guest, machine.command = guest, command
    machine.upload(payload, "/tmp/pd-fixture-plan.json")
    assert len(probes) == 1 and events[0] == ("guest", ("true",))
    check = events[-1][1][-1]
    assert "O_NOFOLLOW" in check and "s.st_uid==1000" in check and sha256(payload.read_bytes()).hexdigest() in check


def test_builder_download_to_verified_staging_then_private_copy(tmp_path, monkeypatch):
    archive = b"offline archive"
    digest = sha256(archive).hexdigest()
    probes = []
    monkeypatch.setattr(staging, "snap_probe", lambda p, d: probes.append(p))
    machine = fixture_execution.FixtureVM(SESSION, "builder")
    def guest(*args, **kwargs):
        if args[0] == "install":
            return b""
        assert "build" in args
        return json.dumps({"archive": digest, "toolchain": {}}).encode()
    machine.guest = guest
    machine.remote_fingerprint = lambda p: {"size": len(archive), "sha256": digest}
    def transfer(*args, **kwargs):
        assert args[0] == "transfer" and args[1].startswith(machine.name + ":")
        staged = Path(args[2])
        assert staged.is_relative_to(Path.home() / "PortfolioDeploymentE2E" / SESSION)
        assert staged.stat().st_mode & 0o777 == 0o600
        staged.write_bytes(archive)
    machine.command = transfer
    result = machine.build(tmp_path)
    assert result["archive"] == digest and len(probes) == 1
    assert (tmp_path / "images.tar").read_bytes() == archive
    assert (tmp_path / "images.tar").stat().st_mode & 0o777 == 0o600
    assert probes[0].read_bytes() == archive  # Retained, never moved/deleted.


@pytest.mark.parametrize("failure", ["hash", "size", "transfer"])
def test_download_failure_retains_staging_without_private_artifact(tmp_path, failure):
    destination = tmp_path / "images.tar"
    exchange = staging.Staging(SESSION, "builder", probe=lambda *args: None)
    payload = b"retained partial artifact"
    def transfer(*args, **kwargs):
        Path(args[2]).write_bytes(payload)
        if failure == "transfer":
            raise multipass.MultipassError("transfer", "timeout", True)
    digest = "0" * 64 if failure == "hash" else sha256(payload).hexdigest()
    with pytest.raises(DockerError):
        exchange.receive(transfer, "vm:/file", destination, digest,
                         size=len(payload) + (1 if failure == "size" else 0))
    assert not destination.exists()
    retained = list(exchange.root.glob(f"{SESSION}/builder-*/images.tar"))
    assert len(retained) == 1 and retained[0].read_bytes() == payload


def test_cleanup_denial_requires_exact_resource_approval(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from contextlib import nullcontext
    from scripts.e2e import cleanup
    from scripts.reconciliation import DesiredState
    monkeypatch.setattr(cleanup, "host_lock", nullcontext)
    monkeypatch.setattr(cleanup, "build_desired_state", lambda *a: DesiredState("a" * 40, ()))
    monkeypatch.setattr(cleanup, "discover", lambda *a: None)
    target = SimpleNamespace(kind="container", identifier="immutable-id", name="owned-name")
    monkeypatch.setattr(cleanup, "cleanup_plan", lambda *a: (target,))
    scope = SimpleNamespace(session=SESSION, directory=tmp_path)
    adapter = SimpleNamespace(_run=lambda *a, **kw: pytest.fail("unapproved removal"))
    preview = cleanup.clean(scope, adapter, set(), "a" * 40, preview=True)
    assert preview["targets"] == [{"kind": "container", "id": "immutable-id", "name": "owned-name"}]
    for approved in (None, [], [{"kind": "container", "id": "changed-id", "name": "owned-name"}]):
        with pytest.raises(DockerError, match="not-approved-or-changed"):
            cleanup.clean(scope, adapter, set(), "a" * 40, approved=approved)


def test_snap_probe_hash_mismatch_is_closed(monkeypatch):
    monkeypatch.setattr(staging.shutil, "which", lambda _: "/usr/bin/snap")
    monkeypatch.setattr(staging, "run", lambda *a, **kw: b"1\n" + b"f" * 64 + b"  file\n")
    with pytest.raises(DockerError, match="staging-integrity-failed"):
        staging.snap_probe(Path.home() / "file", {"size": 1, "sha256": "0" * 64})


@pytest.mark.parametrize("unsafe", ["symlink", "group-write"])
def test_unsafe_staging_root_has_specific_diagnostic(tmp_path, unsafe):
    root = Path.home() / "PortfolioDeploymentE2E"
    if unsafe == "symlink":
        target = tmp_path / "other"
        target.mkdir(mode=0o700)
        root.symlink_to(target)
    else:
        root.mkdir(mode=0o770)
        root.chmod(0o770)
    exchange = staging.Staging(SESSION, "builder", probe=lambda *a: pytest.fail("unsafe directory reached Snap"))
    with pytest.raises(DockerError, match="staging-unsafe-directory"):
        with exchange.operation():
            pytest.fail("unsafe directory accepted")
