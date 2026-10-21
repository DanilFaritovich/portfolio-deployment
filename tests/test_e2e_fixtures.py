"""Fixture publication contracts with synthetic archives and transports only."""
from hashlib import sha256
import io
import json
from pathlib import Path
import socket
import subprocess
import tarfile

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import cli, fixture_bundle, fixture_execution as execution, fixture_guest as guest
from scripts.e2e.host_state import Store
from scripts.e2e.images import Images, ROLES

BASE = "docker.io/library/python@sha256:" + "a" * 64
PHASES = ["builder-prepare", "build", "publisher-prepare", "publish", "verify-public"]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    isolated_home = tmp_path / "host-home"
    isolated_home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setattr(cli.paths, "ROOT", tmp_path / "checkout")
    monkeypatch.chdir(tmp_path)
    def forbidden(*a, **kw):
        pytest.fail("real processes/network forbidden")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    from scripts.e2e import staging
    monkeypatch.setattr(staging, "snap_probe", lambda path, expected: None)


def artifact(role):
    config = json.dumps({"os": "linux", "architecture": "amd64", "config": {"Env": ["ROLE=" + role]}}).encode()
    blob = sha256(config).hexdigest()
    raw = json.dumps({"schemaVersion": 2, "mediaType": guest.MANIFEST,
                      "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                 "digest": "sha256:" + blob, "size": len(config)}, "layers": []}).encode()
    return raw, config, blob


def archive(path):
    with tarfile.open(path, "x:") as tar:
        for role in sorted(ROLES):
            raw, config, blob = artifact(role)
            for name, value in [("manifest.json", raw), (blob, config), ("version", b"Directory Transport Version: 1.1\n")]:
                entry = tarfile.TarInfo(role + "/" + name)
                entry.size = len(value)
                tar.addfile(entry, io.BytesIO(value))


class FakeVM:
    instances = []

    def __init__(self, session, kind):
        self.session, self.kind = session, kind
        self.name = f"pd-fixture-{kind}-{session}"
        self.calls = []
        self.instances.append(self)

    def prerequisites(self):
        return {"status": "passed"}

    def prepare_fixture(self, directory):
        self.directory = directory
        self.calls.append("prepare")
        return self.kind + "-machine"

    def build(self, directory):
        self.calls.append("build")
        archive(directory / "images.tar")
        return {"archive": guest.digest_file(directory / "images.tar"), "toolchain": {"buildah": "synthetic", "skopeo": "synthetic"}}

    def references(self):
        plan = json.loads((self.directory / "plan.json").read_bytes())
        return {"references": {role: plan["images"][role]["repository"] + "@sha256:" + sha256(artifact(role)[0]).hexdigest()
                               for role in sorted(ROLES)}}

    def publish(self, directory):
        self.calls.append("publish")
        return self.references()

    def verify_public(self):
        self.calls.append("verify-public")
        return self.references()


def test_execute_exact_digests_confirmations_and_no_rebuild(tmp_path):
    directory = tmp_path / "bundle"
    approvals = []
    def consent(message, token):
        approvals.append(token.split(":")[0])
        with open(directory / "execution.json") as stream:
            assert json.load(stream)["status"] == "planned"
    result = execution.execute(directory, "fixtures", BASE, factory=FakeVM, consent=consent)
    assert result["status"] == "passed" and result["phase"] == "complete"
    assert approvals == PHASES
    images = Images(result["lock"])
    assert images.records == result["references"]
    for role in ROLES:
        assert images.records[role].endswith(sha256(artifact(role)[0]).hexdigest())
        assert not images.records[role].endswith(artifact(role)[2])  # Config/image ID is not the manifest digest.
    again = execution.execute(directory, "fixtures", BASE, factory=lambda *a: pytest.fail("rebuild"),
                              consent=lambda *a: pytest.fail("repeat confirmation"))
    assert again == result
    assert (directory / "images.tar").exists()


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("failure", ["decline", "disconnect", "interrupt", "unknown"])
def test_failure_preserves_state_and_blocks_retry(tmp_path, phase, failure):
    directory = tmp_path / "bundle"
    def consent(message, token):
        if token.startswith(phase + ":") and failure == "decline":
            raise DockerError("e2e-operation-declined")
    class Broken(FakeVM):
        def fail(self, here):
            if here != phase or failure == "decline":
                return
            if failure == "interrupt":
                raise KeyboardInterrupt()
            if failure == "disconnect":
                raise DockerError("connection-lost", True)
            raise ValueError("private registry response must never be recorded")
        def prepare_fixture(self, directory):
            self.fail(self.kind + "-prepare")
            return super().prepare_fixture(directory)
        def build(self, directory):
            self.fail("build")
            return super().build(directory)
        def publish(self, directory):
            self.fail("publish")
            return super().publish(directory)
        def verify_public(self):
            self.fail("verify-public")
            return super().verify_public()
    result = execution.execute(directory, "fixtures", BASE, factory=Broken, consent=consent)
    assert result["status"] == ("blocked" if failure == "decline" else "unknown")
    assert not (directory / "artifacts").exists()
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(DockerError, match="global-unfinished-review|unfinished-no-retry"):
        execution.execute(directory, "fixtures", BASE, factory=lambda *a: pytest.fail("retry"))
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert b"private registry response" not in (directory / "execution.json").read_bytes()


@pytest.mark.parametrize("change", ["traversal", "link", "duplicate", "index", "config-platform", "config-digest"])
def test_archive_rejects_unsafe_or_unverifiable_artifacts(tmp_path, change):
    path = tmp_path / "images.tar"
    if change in {"traversal", "link", "duplicate"}:
        with tarfile.open(path, "x:") as tar:
            entry = tarfile.TarInfo("v1/version" if change == "duplicate" else "../escape")
            if change == "link":
                entry.name, entry.type, entry.linkname = "v1/version", tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(entry, io.BytesIO(b""))
            if change == "duplicate":
                tar.addfile(entry, io.BytesIO(b""))
    else:
        archive(path)
        guest.unpack(path, tmp_path / "valid")
        directory = tmp_path / "valid/v1"
        raw, config, blob = artifact("v1")
        if change == "index":
            (directory / "manifest.json").write_text('{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}')
        elif change == "config-platform":
            config = config.replace(b"amd64", b"arm64")
            new = sha256(config).hexdigest()
            (directory / new).write_bytes(config)
            (directory / "manifest.json").write_bytes(raw.replace(blob.encode(), new.encode()))
        else:
            (directory / blob).write_bytes(b"x" * len(config))
        with pytest.raises(ValueError):
            guest.validate_directory(directory)
        return
    with pytest.raises(ValueError):
        guest.unpack(path, tmp_path / "extracted")
    assert not (tmp_path / "escape").exists()


def test_public_verification_raw_bytes_config_and_anonymous_auth(tmp_path, monkeypatch):
    archive(tmp_path / "images.tar")
    guest.unpack(tmp_path / "images.tar", tmp_path / "publish-input")
    plan = {"images": {role: {"repository": "ghcr.io/fixtures/pd-e2e-" + role} for role in ROLES}}
    calls = []
    def command(*args):
        calls.append(args)
        assert "--retry-times=0" in args and "--raw" in args
        assert json.loads(Path(args[args.index("--authfile") + 1]).read_text()) == {"auths": {}}
        role = args[-1].split("pd-e2e-")[1].split("@")[0]
        return artifact(role)[1 if "--config" in args else 0]
    monkeypatch.setattr(guest, "command", command)
    result = guest.verify_public(plan, tmp_path)
    assert set(result["references"]) == ROLES and len(calls) == 12
    assert len(list(tmp_path.glob("*.public.json"))) == 6


def test_public_verification_rejects_manifest_reserialization(tmp_path, monkeypatch):
    archive(tmp_path / "images.tar")
    guest.unpack(tmp_path / "images.tar", tmp_path / "publish-input")
    monkeypatch.setattr(guest, "command", lambda *a: artifact("bad")[0] + b"\n")
    with pytest.raises(ValueError):
        guest.verify_public({"images": {role: {"repository": "ghcr.io/fixtures/pd-e2e-" + role} for role in ROLES}}, tmp_path)


def test_cli_execute_opt_in_and_default_ready_lock(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda owner: owner)
    monkeypatch.setattr(cli.orchestration, "confirm", lambda *a: calls.append("confirm"))
    monkeypatch.setattr(cli.bootstrap, "resolve_base", lambda *a: BASE)
    monkeypatch.setattr(cli.fixture_execution, "execute", lambda *a, **kw: calls.append("execute") or {"status": "passed"})
    args = ["fixtures", "--output", str(tmp_path / "bundle"), "--owner", "fixtures", "--base", BASE]
    assert cli.main(args + ["--plan"]) == 2 and calls == []
    assert cli.main(args + ["--execute"]) == 0 and calls == ["confirm", "execute"]
    assert cli.main(args + ["--execute", "--manifests", str(tmp_path)]) == 2


def test_guest_build_verifies_base_source_and_preserves_healthcheck(tmp_path, monkeypatch):
    fixture_bundle.prepare(tmp_path / "bundle", "fixtures", BASE)
    root = tmp_path / "bundle"
    plan = json.loads((root / "plan.json").read_bytes())
    base_raw, base_config, _ = artifact("base")
    plan["base"] = "docker.io/library/python@sha256:" + sha256(base_raw).hexdigest()
    calls = []
    def command(*args):
        calls.append(args)
        if args[0] == "skopeo" and args[1] == "inspect":
            return base_config if "--config" in args else base_raw
        if "--version" in args:
            return (args[0] + " test-version").encode()
        assert args[0] == "buildah" and "--retry=0" in args
        if args[1] == "bud":
            assert "--format=docker" in args and "--rm=false" in args and "--force-rm=false" in args
            assert "--timestamp=0" in args and "--platform=linux/amd64" in args
        else:
            assert args[1] == "push" and "--format=v2s2" in args
            output = Path(args[-1].removeprefix("dir:"))
            output.mkdir()
            role = output.name
            config = json.loads(artifact(role)[1])
            config["config"]["Healthcheck"] = {"Test": ["CMD", "python3", "/fixture/app.py", "healthcheck"]}
            config = json.dumps(config).encode()
            digest = sha256(config).hexdigest()
            manifest = json.loads(artifact(role)[0])
            manifest["config"].update(digest="sha256:" + digest, size=len(config))
            (output / "manifest.json").write_text(json.dumps(manifest))
            (output / digest).write_bytes(config)
            (output / "version").write_text("Directory Transport Version: 1.1\n")
        return b""
    monkeypatch.setattr(guest, "command", command)
    result = guest.build(plan, root)
    assert result["archive"] == guest.digest_file(root / "images.tar")
    assert len([c for c in calls if c[:2] == ("buildah", "bud")]) == 6
    assert len(list(root.glob("*-build.result.json"))) == 6
    before = len(calls)
    with pytest.raises(FileExistsError):
        guest.build(plan, root)
    assert len(calls) == before


@pytest.mark.parametrize("change", ["source", "base-index", "base-digest", "platform"])
def test_guest_build_refuses_bad_inputs_before_build(tmp_path, monkeypatch, change):
    root = tmp_path / "bundle"
    fixture_bundle.prepare(root, "fixtures", BASE)
    plan = json.loads((root / "plan.json").read_bytes())
    raw, config, _ = artifact("base")
    if change == "source":
        (root / "context/app.py").write_text("tampered")
    elif change == "base-index":
        raw = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
    elif change == "platform":
        config = config.replace(b"amd64", b"arm64")
        manifest = json.loads(raw)
        manifest["config"]["digest"] = "sha256:" + sha256(config).hexdigest()
        raw = json.dumps(manifest).encode()
    if change != "base-digest":
        plan["base"] = "docker.io/library/python@sha256:" + sha256(raw).hexdigest()
    calls = []
    def command(*args):
        calls.append(args)
        assert args[:2] == ("skopeo", "inspect")
        return config if "--config" in args else raw
    monkeypatch.setattr(guest, "command", command)
    with pytest.raises(ValueError):
        guest.build(plan, root)
    assert not (root / "images.tar").exists()
    assert (root / "build.intent.json").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_publisher_preserves_digests_and_removes_only_ephemeral_auth(tmp_path, monkeypatch, failure):
    archive(tmp_path / "images.tar")
    auth = tmp_path / "auth.json"
    auth.write_text('{"auths":{"ghcr.io":{"auth":"private-sentinel"}}}')
    auth.chmod(0o600)
    # Simulate guest root ownership without requiring root for offline tests.
    original_path = guest.Path
    class AuthPath(type(auth)):
        def lstat(self):
            value = super().lstat()
            from types import SimpleNamespace
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
        def is_file(self):
            return True
        def is_symlink(self):
            return False
    monkeypatch.setattr(guest, "Path", lambda value: AuthPath(auth) if value == "/run/pd-fixtures-auth.json" else original_path(value))
    calls = []
    def command(*args):
        calls.append(args)
        assert "--retry-times=0" in args and "private-sentinel" not in str(args)
        if failure:
            raise ValueError("sensitive registry response")
        if args[1] == "copy":
            assert "--preserve-digests" in args
            assert args[-2].startswith("dir:") and args[-1].startswith("docker://ghcr.io/fixtures/pd-e2e-")
            return b""
        role = args[-1].split("pd-e2e-")[1].split("@")[0]
        return artifact(role)[0]
    monkeypatch.setattr(guest, "command", command)
    plan = {"images": {role: {"repository": "ghcr.io/fixtures/pd-e2e-" + role} for role in ROLES}}
    if failure:
        with pytest.raises(ValueError):
            guest.publish(plan, "a" * 16, tmp_path)
        assert len(calls) == 1
    else:
        assert set(guest.publish(plan, "a" * 16, tmp_path)["references"]) == ROLES
    assert not auth.exists()
    assert (tmp_path / "images.tar").exists() and (tmp_path / "publish.intent.json").exists()
    assert b"private-sentinel" not in b"".join(path.read_bytes() for path in tmp_path.glob("*.json"))


def test_fixture_vm_prepare_transport_has_no_daemon_mount_or_credentials(tmp_path, monkeypatch):
    # Stage sources under the isolated HOME because /tmp itself is shared.
    tmp_path = Path.home() / "fixture-source"
    tmp_path.mkdir(mode=0o700)
    fixture_bundle.prepare(tmp_path / "bundle", "fixtures", BASE)
    directory = tmp_path / "bundle"
    (directory / "fixture_guest.py").write_bytes(b"synthetic payload")
    (directory / "fixture_guest.py").chmod(0o600)
    for kind in ("builder", "publisher"):
        machine = execution.FixtureVM("a" * 16, kind)
        calls = []
        def command(*args, **kw):
            calls.append(args)
            if args[:1] == ("list",):
                return b'{"list":[]}'
            if args[:1] == ("info",):
                return json.dumps({"info": {machine.name: {"state": "Running", "mounts": {}}}}).encode()
            if args[:1] == ("exec",) and "-c" in args:
                return b"machine-id\n"
            return b""
        machine.command = command
        assert machine.prepare_fixture(directory) == "machine-id"
        (directory / "fixture_guest.py").chmod(0o600)
        assert (directory / "fixture_guest.py").stat().st_mode & 0o777 == 0o600
        cloud = (directory / (kind + "-cloud-init.yaml")).read_text()
        assert "docker" not in cloud.lower() and "mounts" not in cloud and "auth" not in cloud.lower()
        assert "skopeo" in cloud and ("buildah" in cloud) == (kind == "builder")
        transferred = [c[1] for c in calls if c[0] == "transfer"]
        assert all(Path(p).name in {"plan.json", "Dockerfile", "app.py", "fixture_guest.py"} for p in transferred)
        assert (any(p.endswith("app.py") for p in transferred)) == (kind == "builder")
        assert all(c[0] in {"list", "launch", "info", "exec", "transfer"} for c in calls)
        assert not any("/run/docker.sock" in str(c) or "delete" in c or "mount" in c for c in calls)


def test_fixture_vm_refuses_adoption_identity_changes_and_host_mounts():
    machine = execution.FixtureVM("a" * 16, "builder")
    machine.command = lambda *a, **kw: json.dumps({"list": [{"name": machine.name}]}).encode()
    with pytest.raises(DockerError, match="exists-no-adoption"):
        machine.prepare_fixture(Path("unused"))
    machine.info = lambda: {"state": "Running"}
    machine.pinned = "original-machine"
    machine.command = lambda *a, **kw: b"replacement-machine"
    with pytest.raises(DockerError, match="identity-changed"):
        machine.guest("true")
    machine = execution.FixtureVM("a" * 16, "publisher")
    machine.command = lambda *a, **kw: json.dumps({"info": {machine.name: {"state": "Running", "mounts": {"/host": {}}}}}).encode()
    with pytest.raises(DockerError, match="host-mounts"):
        machine.guest("true")


def test_fixture_controller_lock_tamper_and_crash_intent(tmp_path):
    directory = tmp_path / "bundle"
    def consent(message, token):
        with pytest.raises(DockerError, match="controller-busy"):
            with Store(directory).locked():
                pytest.fail("lost whole-operation flock")
        if token.startswith("build:"):
            (directory / "context/app.py").write_text("changed during confirmation")
    result = execution.execute(directory, "fixtures", BASE, factory=FakeVM, consent=consent)
    assert result["status"] == "blocked" and result["reason"] == "fixture-source-changed"
    assert not (directory / "images.tar").exists()


def test_fixture_crash_intent_blocks_replay(tmp_path):
    directory = tmp_path / "crash"
    class Crash(FakeVM):
        def build(self, directory):
            raise SystemExit(9)
    with pytest.raises(SystemExit):
        execution.execute(directory, "fixtures", BASE, factory=Crash, consent=lambda *a: None)
    with Store(directory).locked() as store:
        state = store.read("execution.json")
    assert state["phase"] == "build" and state["status"] == "unknown"
    with pytest.raises(DockerError, match="global-unfinished-review|unfinished-no-retry"):
        execution.execute(directory, "fixtures", BASE, factory=lambda *a: pytest.fail("crash replay"))


def test_candidate_validation_precedes_publisher_and_final_lock_failure_keeps_evidence(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    class Invalid(FakeVM):
        def build(self, directory):
            result = super().build(directory)
            return result
    monkeypatch.setattr(execution, "create", lambda *a: (_ for _ in ()).throw(ValueError("invalid candidate")))
    FakeVM.instances = []
    result = execution.execute(directory, "fixtures", BASE, factory=Invalid, consent=lambda *a: None)
    assert result["status"] == "unknown"
    assert FakeVM.instances[-1].kind == "publisher" and FakeVM.instances[-1].calls == []
    assert (directory / "raw/v1.json").exists() and (directory / "images.tar").exists()


def test_final_lock_failure_keeps_evidence(tmp_path, monkeypatch):
    directory = tmp_path / "lock-failure"
    monkeypatch.setattr(execution, "import_manifests", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    result = execution.execute(directory, "fixtures", BASE, factory=FakeVM, consent=lambda *a: None)
    assert result["phase"] == "lock" and result["status"] == "unknown"
    assert set(result["references"]) == ROLES
    assert (directory / "raw/lock.json").exists() and not (directory / "artifacts").exists()
