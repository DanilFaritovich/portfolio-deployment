"""Unified first/repeated run and official base resolution, entirely offline."""
from hashlib import sha256
import json
import socket
import subprocess

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import bootstrap, cli, paths
from scripts.e2e.host_state import Store
from test_e2e_fixtures import artifact, BASE, FakeVM
from scripts.e2e import fixture_execution


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "ROOT", tmp_path / "checkout")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("E2E_STATE_DIR", raising=False)
    monkeypatch.delenv("E2E_FIXTURE_DIR", raising=False)
    def forbidden(*a, **kw):
        pytest.fail("real process/network forbidden")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)


@pytest.mark.parametrize("url", ["https://github.com/Some-Owner/Repo.git", "git@github.com:Some-Owner/Repo.git",
                                "ssh://git@github.com/Some-Owner/Repo"])
def test_origin_lowercase_and_override(url):
    run = lambda *a, **kw: (url + "\n").encode()
    assert bootstrap.owner_from_origin(run=run) == "some-owner"
    assert bootstrap.owner_from_origin("Other-Owner", run=run) == "other-owner"


@pytest.mark.parametrize("urls", [[], ["https://github.com/a/b", "https://github.com/c/b"],
    ["https://evil.example/a/b"], ["https://github.com.evil/a/b"], ["https://user@github.com/a/b"],
    ["git@github.com:a/b\ngit@github.com:c/d"], ["https://github.com/a/b?x=1"],
    ["https://github.com/a/b/extra"]])
def test_origin_rejects_unknown_or_ambiguous_even_with_override(urls):
    def run(args, **kw):
        if not urls:
            raise DockerError("missing-origin")
        return (urls[-1] if "--push" in args else urls[0]).encode()
    with pytest.raises(DockerError, match="github-origin"):
        bootstrap.owner_from_origin("override", run=run)


def metadata(change=""):
    _, config, _ = artifact("base")
    config = json.loads(config)
    config["config"]["Env"] = ["PYTHON_VERSION=3.13.9"]
    if change == "platform":
        config["architecture"] = "arm64"
    if change == "python":
        config["config"]["Env"] = []
    config = json.dumps(config).encode()
    manifest = json.loads(artifact("base")[0])
    manifest["config"].update(digest="sha256:" + sha256(config).hexdigest(), size=len(config))
    raw = json.dumps(manifest).encode()
    child = {"digest": "sha256:" + sha256(raw).hexdigest(), "size": len(raw),
             "platform": {"os": "linux", "architecture": "amd64"}}
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [child]}
    if change == "ambiguous":
        index["manifests"].append(child)
    if change == "missing":
        index["manifests"] = []
    calls = []
    def get(url, headers=None):
        calls.append(url)
        if "auth.docker.io" in url:
            return b'{"token":"anonymous-token"}'
        if "/blobs/" in url:
            return config + (b" " if change == "config-digest" else b"")
        if url.endswith("3.13-slim-bookworm"):
            return json.dumps(index).encode()
        return raw + (b" " if change == "manifest-digest" else b"")
    return get, calls, raw


def test_resolve_direct_child_freezes_before_build_and_never_uses_tag_again(tmp_path):
    get, calls, raw = metadata()
    expected = bootstrap.REPOSITORY + "@sha256:" + sha256(raw).hexdigest()
    assert bootstrap.resolve_base(tmp_path / "private", get=get) == expected
    assert len(calls) == 4 and calls[2].endswith(expected.split("@")[1])
    with Store(tmp_path / "private").locked() as store:
        pin = store.read("python-base.json")
        assert bootstrap.verify_pin(pin) == expected
    assert bootstrap.resolve_base(tmp_path / "private", get=lambda *a: pytest.fail("tag reused")) == expected
    assert bootstrap.resolve_base(tmp_path / "private", expected, get=lambda *a: pytest.fail("refetch")) == expected
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("change", ["platform", "python", "ambiguous", "missing", "config-digest", "manifest-digest"])
def test_bad_base_never_pinned(tmp_path, change):
    get, _, _ = metadata(change)
    with pytest.raises((ValueError, DockerError)):
        bootstrap.resolve_base(tmp_path / "private", get=get)
    assert not (tmp_path / "private/python-base.json").exists()


def test_direct_override_must_match_exact_manifest(tmp_path):
    get, _, _ = metadata()
    with pytest.raises(DockerError, match="digest-mismatch"):
        bootstrap.resolve_base(tmp_path / "private", BASE, get=get)
    with pytest.raises(DockerError, match="official-immutable"):
        bootstrap.resolve_base(tmp_path / "other", "ghcr.io/untrusted/python@sha256:" + "a" * 64, get=get)


def test_unified_first_and_repeat_run_without_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in list(__import__("os").environ):
        if key.startswith("E2E_"):
            monkeypatch.delenv(key)
    calls = []
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda owner: "fixtures")
    monkeypatch.setattr(cli.bootstrap, "resolve_base", lambda *a: calls.append("resolve") or BASE)
    monkeypatch.setattr(cli.orchestration, "confirm", lambda message, token: calls.append(token))
    original = fixture_execution.execute
    monkeypatch.setattr(cli.fixture_execution, "execute", lambda *a, **kw: original(*a, **kw, factory=FakeVM,
                        consent=lambda message, token: calls.append(token.split(":")[0])))
    monkeypatch.setattr(cli.orchestration, "run", lambda lock, state: calls.append("run") or {"status": "passed"})
    assert cli.main(["run"]) == 0
    assert calls == ["prepare-fixtures", "resolve", "builder-prepare", "build", "publisher-prepare", "publish", "verify-public", "run"]
    calls.clear()
    monkeypatch.setattr(cli.bootstrap, "resolve_base", lambda *a: pytest.fail("rebuild"))
    assert cli.main(["run"]) == 0 and calls == ["run"]
    with Store(paths.resolve("fixtures")).locked() as store:
        state = store.read("execution.json")
        state["status"] = "unknown"
        store.write("execution.json", state)
    calls.clear()
    assert cli.main(["run"]) == 2 and calls == []


def test_corrupt_existing_lock_does_not_trigger_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda owner: "fixtures")
    bundle = tmp_path / "bundle"
    with Store(bundle).locked() as store:
        (store.path / "artifacts").mkdir(mode=0o700)
        (store.path / "artifacts/lock.json").write_text("{}")
    monkeypatch.setattr(cli.orchestration, "confirm", lambda *a: pytest.fail("rebuild"))
    assert cli.main(["run", "--output", str(bundle), "--state-dir", str(tmp_path / "state")]) == 2
    assert "help" in json.loads(capsys.readouterr().out)


def test_noninteractive_default_fixture_preparation_denies_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda owner: "fixtures")
    monkeypatch.setattr(cli.orchestration.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.bootstrap, "resolve_base", lambda *a: pytest.fail("unconfirmed metadata"))
    assert cli.main(["fixtures", "--output", str(tmp_path / "private")]) == 2
    assert not (tmp_path / "private/execution.json").exists()


def test_fixture_prerequisites_list_missing_and_allow_correction_before_intent(tmp_path):
    class Missing(FakeVM):
        def prerequisites(self):
            return {"status": "blocked", "checks": {"multipass": False, "hostDisk64GiB": False}}
    directory = tmp_path / "private"
    result = fixture_execution.execute(directory, "fixtures", BASE, factory=Missing,
                                       consent=lambda *a: pytest.fail("VM confirmation"))
    assert result["missing"] == ["multipass", "hostDisk64GiB"]
    assert not (directory / "execution.json").exists()
    assert not (directory / "fixture_guest.py").exists()
    assert fixture_execution.execute(directory, "fixtures", BASE, factory=FakeVM, consent=lambda *a: None)["status"] == "passed"


def test_anonymous_redirect_never_forwards_registry_token():
    request = bootstrap.urllib.request.Request("https://registry-1.docker.io/v2/library/python/blobs/sha256:x",
                                              headers={"Authorization": "Bearer sentinel"})
    redirected = bootstrap.PublicRedirect().redirect_request(request, None, 302, "Found", {}, "https://cdn.example/config")
    assert not redirected.has_header("Authorization")
    with pytest.raises(DockerError, match="insecure-redirect"):
        bootstrap.PublicRedirect().redirect_request(request, None, 302, "Found", {}, "http://cdn.example/config")


def test_tampered_base_pin_never_refetches(tmp_path):
    directory = tmp_path / "private"
    get, _, _ = metadata()
    bootstrap.resolve_base(directory, get=get)
    with Store(directory).locked() as store:
        pin = store.read("python-base.json")
        pin["config"] += " "
        store.write("python-base.json", pin)
    with pytest.raises(DockerError, match="digest-mismatch"):
        bootstrap.resolve_base(directory, get=lambda *a: pytest.fail("automatic refetch"))


def test_unfinished_e2e_prevents_initial_fixture_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.bootstrap, "owner_from_origin", lambda owner: "fixtures")
    monkeypatch.setattr(cli.orchestration, "confirm", lambda *a: pytest.fail("fixture work after unknown E2E"))
    session = "a" * 16
    with Store(paths.resolve("state")).locked() as store:
        store.write("current.json", {"version": 1, "session": session})
        store.write(session + ".json", {"version": 1, "session": session, "vm": "pd-e2e-" + session,
                    "phase": "run", "status": "unknown", "mutationStarted": True, "images": "b" * 64,
                    "identity": {}, "result": {}})
    assert cli.main(["run"]) == 2
    assert not paths.resolve("fixtures").exists()
