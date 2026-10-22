"""Offline host path security and checkout-independent operator entry points."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts.deployment_journal import JournalError
from scripts.docker_adapter import DockerError
from scripts.e2e import cli, paths
from scripts.e2e.host_state import Store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for name in ("E2E_STATE_DIR", "E2E_FIXTURE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths, "ROOT", tmp_path / "checkout")
    return tmp_path


def test_first_creation_repeat_lock_and_receipts(home):
    target = paths.resolve("state")
    assert target == home / ".local/share/portfolio-deployment/e2e-state"
    assert not target.exists()
    with Store(target).locked() as store:
        store.write("receipt.json", {"status": "unknown"})
        inode = (target / "controller.lock").stat().st_ino
        with pytest.raises(DockerError, match="controller-busy"):
            with Store(target).locked():
                pass
    with pytest.raises(RuntimeError):
        with Store(target).locked() as store:
            assert store.read("receipt.json") == {"status": "unknown"}
            raise RuntimeError("interrupted")
    assert (target / "controller.lock").stat().st_ino == inode
    for directory in (home / ".local", home / ".local/share", target.parent, target):
        assert directory.stat().st_mode & 0o777 == 0o700
    for name in ("controller.lock", "receipt.json"):
        assert (target / name).stat().st_mode & 0o777 == 0o600
    assert not (target / "receipt.json.pending").exists()


def test_defaults_independent_of_775_checkout(home, monkeypatch, capsys):
    checkout = home / "Work/checkout"
    checkout.mkdir(parents=True)
    checkout.parent.chmod(0o775)
    checkout.chmod(0o775)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda _: "fixtures")
    monkeypatch.setattr(cli.orchestration.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.bootstrap, "resolve_base", lambda *a: pytest.fail("network"))
    assert cli.main(["run"]) == 2
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["reason"] == "e2e-interactive-confirmation-required"
    assert paths.resolve("state").is_dir() and paths.resolve("fixtures").is_dir()
    assert not (checkout / ".e2e-state").exists()
    assert checkout.stat().st_mode & 0o777 == 0o775
    assert checkout.parent.stat().st_mode & 0o777 == 0o775


@pytest.mark.parametrize("kind,variable", [("state", "E2E_STATE_DIR"), ("fixtures", "E2E_FIXTURE_DIR")])
def test_overrides(home, monkeypatch, kind, variable):
    target = home / "override/nested/private"
    monkeypatch.setenv(variable, str(target))
    assert paths.resolve(kind) == target
    with Store(paths.resolve(kind)).locked():
        pass
    assert target.stat().st_mode & 0o777 == 0o700
    monkeypatch.setenv(variable, "relative")
    assert paths.resolve(kind) == Path.cwd() / "relative"
    monkeypatch.setenv(variable, "~/../escape")
    with pytest.raises(DockerError, match="traversal"):
        paths.resolve(kind)


@pytest.mark.parametrize("leaf", [True, False])
def test_unsafe_permissions_unchanged(home, leaf):
    target = home / "unsafe"
    target.mkdir(mode=0o775)
    target.chmod(0o775)
    with pytest.raises(DockerError, match="permissions-or-owner"):
        with Store(target if leaf else target / "private").locked():
            pytest.fail("unsafe path accepted")
    assert target.stat().st_mode & 0o777 == 0o775
    assert not (target / "private").exists()


def test_unsafe_owner(home, monkeypatch):
    target = home / "foreign"
    target.mkdir(mode=0o700)
    original = os.fstat
    inode = target.stat().st_ino
    def fstat(fd):
        info = original(fd)
        if info.st_ino == inode:
            return SimpleNamespace(st_uid=123456789, st_mode=info.st_mode)
        return info
    monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(DockerError, match="permissions-or-owner"):
        with Store(target / "private").locked():
            pytest.fail("foreign owner")
    assert not (target / "private").exists()


def test_symlink_and_traversal(home):
    destination = home / "destination"
    destination.mkdir(mode=0o700)
    link = home / "link"
    link.symlink_to(destination, target_is_directory=True)
    for target in (link, link / "private"):
        with pytest.raises(JournalError, match="symlink"):
            with Store(target).locked():
                pytest.fail("symlink accepted")
    with pytest.raises(DockerError, match="traversal"):
        Store(home / "x/../private")
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("legacy", [".e2e-state", ".e2e-fixtures"])
@pytest.mark.parametrize("status", ["unknown", "passed"])
def test_legacy_blocks_even_overrides_without_modifying_evidence(home, monkeypatch, capsys, legacy, status):
    checkout = paths.ROOT
    old = checkout / legacy
    old.mkdir(parents=True)
    receipt = old / "execution.json"
    receipt.write_text(json.dumps({"status": status}))
    before = receipt.read_bytes()
    monkeypatch.chdir(home)
    monkeypatch.setenv("E2E_STATE_DIR", str(home / "new-state"))
    monkeypatch.setenv("E2E_FIXTURE_DIR", str(home / "new-fixtures"))
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda _: pytest.fail("origin before legacy guard"))
    assert cli.main(["run"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "e2e-legacy-state-review-required"
    assert receipt.read_bytes() == before
    assert not (home / "new-state").exists()


def test_path_error_specific_and_safe(home, monkeypatch, capsys):
    target = home / "secret-token-sentinel"
    target.mkdir(mode=0o755)
    monkeypatch.setenv("E2E_STATE_DIR", str(target))
    assert cli.main(["report"]) == 2
    result = capsys.readouterr().out
    assert json.loads(result)["reason"] == "e2e-path-unsafe-permissions-or-owner"
    assert "secret-token-sentinel" not in result
    assert "Do not chmod" in result


def test_make_e2e_reaches_confirmation_offline_from_775_checkout(home):
    # Execute the actual Make recipe with a Python shim that forbids live work.
    source = Path(__file__).absolute().parents[1]
    checkout = home / "Work/checkout"
    checkout.mkdir(parents=True)
    checkout.parent.chmod(0o775)
    checkout.chmod(0o775)
    (checkout / "Makefile").write_bytes((source / "Makefile").read_bytes())
    shim = home / "python-offline"
    shim.write_text(f'''#!/bin/sh
exec {os.sys.executable} -c 'from scripts.e2e import cli; cli.paths.ROOT=cli.Path.cwd(); cli.bootstrap.owner_from_origin=lambda _: "fixtures"; cli.orchestration.sys.stdin.isatty=lambda: False; raise SystemExit(cli.main(["run"]))'
''')
    shim.chmod(0o700)
    env = {**os.environ, "HOME": str(home), "E2E_STATE_DIR": "", "E2E_FIXTURE_DIR": "", "PYTHONPATH": str(source)}
    result = subprocess.run(["make", "e2e", f"PYTHON={shim}"], cwd=checkout,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert '"reason": "e2e-interactive-confirmation-required"' in result.stdout
    assert (home / ".local/share/portfolio-deployment/e2e-state/controller.lock").exists()


def test_existing_public_ancestors_and_private_file_rejected_without_repair(home):
    share = home / ".local/share"
    share.mkdir(parents=True, mode=0o755)
    share.chmod(0o755)
    share.parent.chmod(0o755)
    target = paths.resolve("state")
    with Store(target).locked() as store:
        store.write("receipt.json", {"status": "unknown"})
    inode = (target / "controller.lock").stat().st_ino
    receipt = target / "receipt.json"
    receipt.chmod(0o644)
    with pytest.raises(DockerError, match="unsafe-e2e-host-state"):
        with Store(target).locked() as store:
            store.read("receipt.json")
    assert share.stat().st_mode & 0o777 == 0o755
    assert receipt.stat().st_mode & 0o777 == 0o644
    assert (target / "controller.lock").stat().st_ino == inode
    assert receipt.read_text() == '{"status": "unknown"}'


def test_legacy_symlink_fails_closed(home, monkeypatch):
    checkout = paths.ROOT
    checkout.mkdir()
    (checkout / ".e2e-state").symlink_to(home, target_is_directory=True)
    with pytest.raises(DockerError, match="legacy-state-review"):
        paths.check_legacy()
