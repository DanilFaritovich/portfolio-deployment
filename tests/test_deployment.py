"""Stage 05 offline tests. Every Docker interaction uses an explicit fake."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import compose, deployment, deployment_cli, docker_adapter, docker_discovery, local_docker_smoke
from scripts.docker_adapter import DockerError
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import DesiredState, build_reconciliation_plan, encode
from test_planning import root, put, observation, empty

SHA = "a" * 40
SESSION = "0123456789abcdef"
LABEL = compose.LABEL


@pytest.fixture
def app(root):
    def strip_refs(spec):
        spec.pop("configRefs", None)
        spec.pop("secretRefs", None)
        for c in spec["components"]:
            c.pop("configRefs", None)
            c.pop("secretRefs", None)
    put(root, mutate=strip_refs)
    return build_desired_state(root, SHA).applications[0]


def desired(app):
    return DesiredState(SHA, (app,))


def image(reference):
    return docker_discovery.ImageEvidence(reference, "sha256:" + "c" * 64, "linux/amd64",
                                          reference.split("@")[1], True, "{}")


def resource(kind, name, labels, **facts):
    defaults = {"labels": labels, "driver": "bridge" if kind == "network" else "local", "internal": True,
                "options": {}, "scope": "local", "containers": {}}
    defaults.update(facts)
    return docker_discovery.DockerResource(kind, name, name, encode(defaults))


def docker_state(app, present=False):
    artifact = compose.render_compose(app, test_session=SESSION)
    model = artifact.model
    resources = []
    for kind, section in (("network", "networks"), ("volume", "volumes")):
        for key, item in model.get(section, {}).items():
            if item.get("external"):
                labs = {f"{LABEL}.managed": "true", f"{LABEL}.role": "proxy", f"{LABEL}.test-session": SESSION}
            else:
                labs = {**item["labels"], "com.docker.compose.project": artifact.project,
                        f"com.docker.compose.{kind}": key}
            resources.append(resource(kind, item["name"], labs))
    if present:
        for name, c in model["services"].items():
            labs = {**c["labels"], "com.docker.compose.project": artifact.project, "com.docker.compose.service": name}
            resources.append(resource("container", f"{artifact.project}-{name}-1", labs,
                state={"Status": "running", "Health": {"Status": "healthy"}},
                imageId=image(c["image"]).image_id, config={"Image": c["image"]}, host={},
                networks={model["networks"][n]["name"]: {"Aliases": [name, *opts.get("aliases", [])]}
                          for n, opts in c["networks"].items()},
                mounts=[{"Type": "volume", "Name": model["volumes"][m["source"]]["name"],
                         "Destination": m["target"], "RW": True} for m in c.get("volumes", [])]))
    return docker_discovery.DockerObservation(tuple(resources), tuple(image(c["image"]) for c in app.spec["components"]), True)


class FakeAdapter:
    test_session = SESSION

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def authorize(self, artifact):
        assert artifact.project.startswith(f"pd-smoke-{SESSION}-")

    def up(self, artifact, service):
        self.calls.append((artifact.project, service))
        if self.error:
            raise self.error


class Provider:
    def __init__(self, d, o, docker, hook=lambda: None):
        self.values = d, o, docker
        self.hook = hook
        self.calls = 0

    def refresh(self):
        self.calls += 1
        self.hook()
        return self.values


def apply(app, tmp_path, *, observed=None, docker=None, adapter=None, provider=None):
    d = desired(app)
    o = load_observed_state(empty() if observed is None else observed)
    plan = build_reconciliation_plan(d, o)
    adapter = adapter or FakeAdapter()
    provider = provider or Provider(d, o, docker or docker_state(app))
    return deployment.execute(d, o, plan, app.id, adapter, provider, lock_directory=tmp_path / "lock"), adapter


@pytest.mark.parametrize("name,count", [("project-router", 1), ("internal-jobs", 1), ("support-assistant", 3)])
def test_render_variants(root, name, count):
    put(root, name)
    app = build_desired_state(root, SHA).applications[0]
    artifact = compose.render_compose(app)
    m = artifact.model
    assert artifact.project == f"portfolio-{app.id}" == m["name"]
    assert len(m["services"]) == count
    assert m["networks"]["private"]["internal"]
    for c in app.spec["components"]:
        s = m["services"][c["name"]]
        assert s["image"] == c["image"] and s["platform"] == "linux/amd64"
        assert not set(s) & {"ports", "build", "privileged", "command", "entrypoint", "healthcheck", "pid", "network_mode"}
        assert ("proxy" in s["networks"]) == c["proxyNetwork"]
        assert s["labels"][f"{LABEL}.component"] == c["name"]
        if c["proxyNetwork"]:
            assert s["networks"]["proxy"]["aliases"] == [f"{app.id}-{c['name']}"]
    assert ("proxy" in m["networks"]) == bool(app.spec["ingress"])
    if name == "support-assistant":
        assert m["services"]["frontend"]["depends_on"] == {"backend": {"condition": "service_started"}}
        assert m["volumes"]["data"]["name"] == "portfolio-support-assistant-data"


def test_immutable_deterministic_and_scoped_hash(app):
    a = compose.render_compose(app)
    with pytest.raises(FrozenInstanceError):
        a.project = "other"
    detached = a.model
    detached["name"] = "other"
    assert a.model["name"] == a.project
    spec = app.spec
    spec["components"].reverse()
    assert compose.render_compose(replace(app, spec_json=encode(spec))) == a
    next(c for c in spec["components"] if c["name"] == "backend")["image"] = "ghcr.io/fixtures/backend@sha256:" + "b" * 64
    b = compose.render_compose(replace(app, spec_json=encode(spec)))
    assert a.model["services"]["frontend"] == b.model["services"]["frontend"]
    assert a.model["volumes"] == b.model["volumes"]
    assert a.sha256 != b.sha256
    assert compose.render_compose(desired(app).applications[0]) == compose.render_compose(replace(desired(app), git_revision="b" * 40).applications[0])


def test_multiple_mounts(app):
    spec = app.spec
    spec["storage"]["volumes"].append({"name": "other"})
    c = next(c for c in spec["components"] if c["name"] == "backend")
    c["volumeMounts"].append({"volume": "other", "mountPath": "/other"})
    artifact = compose.render_compose(replace(app, spec_json=encode(spec)))
    assert len(artifact.model["services"]["backend"]["volumes"]) == 2
    assert all(m["type"] == "volume" and m["volume"]["nocopy"] for m in artifact.model["services"]["backend"]["volumes"])


def test_create_and_dependency_stop(app, tmp_path):
    results, adapter = apply(app, tmp_path)
    assert [r.status for r in results] == ["applied-unverified", "applied-unverified", "blocked"]
    assert [c[1] for c in adapter.calls] == ["backend", "worker"]
    assert all(not r.document["releaseSuccess"] and not r.document["readinessVerified"] for r in results)
    assert results[-1].reasons == ("dependency-readiness-unverified",)


def test_noop_and_revision_only(app, tmp_path):
    d = desired(app)
    observed = observation(d)
    for _ in range(2):
        results, adapter = apply(app, tmp_path, observed=observed, docker=docker_state(app, True))
        assert {r.status for r in results} == {"no-op"}
        assert not adapter.calls
    o = load_observed_state(observed)
    assert {e["action"] for e in build_reconciliation_plan(replace(d, git_revision="b" * 40), o).document["entries"]} == {"no-op"}


def test_backend_only_update(app, tmp_path):
    observed = observation(desired(app))
    spec = app.spec
    next(c for c in spec["components"] if c["name"] == "backend")["image"] = "ghcr.io/fixtures/backend@sha256:" + "b" * 64
    updated = replace(app, spec_json=encode(spec))
    docker = docker_state(app, True)
    docker = replace(docker, images=docker.images + tuple(image(c["image"]) for c in updated.spec["components"]))
    results, adapter = apply(updated, tmp_path, observed=observed, docker=docker)
    assert [c[1] for c in adapter.calls] == ["backend"]
    assert [r.status for r in results].count("applied-unverified") == 1


@pytest.mark.parametrize("unknown,status", [(False, "failed"), (True, "unknown")])
def test_execution_failure_stops_siblings(app, tmp_path, unknown, status):
    adapter = FakeAdapter(DockerError("docker-timeout" if unknown else "docker-command-failed", unknown))
    results, adapter = apply(app, tmp_path, adapter=adapter)
    assert results[0].status == status
    assert all(r.status == "blocked" for r in results[1:])
    assert len(adapter.calls) == 1
    assert "fresh-discovery" in results[0].next_checks


@pytest.mark.parametrize("change", ["incomplete", "foreign", "degraded", "storage", "mount", "unknown-health", "wrong-project", "runtime-drift"])
def test_blockers_cannot_be_bypassed(app, tmp_path, change):
    d = desired(app)
    observed = observation(d)
    docker = docker_state(app, True)
    if change == "incomplete":
        observed["scope"]["complete"] = False
    elif change == "foreign":
        observed["applications"][0]["owner"]["manager"] = "foreign"
    elif change == "degraded":
        observed["applications"][0]["components"][0]["health"] = "degraded"
    elif change == "storage":
        observed["applications"][0]["storage"] = {"type": "stateless"}
    elif change == "mount":
        next(c for c in observed["applications"][0]["components"] if c["name"] == "backend")["runtime"]["volumeMounts"] = []
    elif change == "unknown-health":
        observed["applications"][0]["components"][0]["health"] = "unknown"
    else:
        records = list(docker.resources)
        i = next(i for i, r in enumerate(records) if r.kind == "container")
        f = records[i].facts
        if change == "wrong-project":
            f["labels"]["com.docker.compose.project"] = "foreign"
        else:
            f["host"]["Privileged"] = True
        records[i] = replace(records[i], facts_json=encode(f))
        docker = replace(docker, resources=tuple(records))
    results, adapter = apply(app, tmp_path, observed=observed, docker=docker)
    assert {r.status for r in results} == {"blocked"} and not adapter.calls


@pytest.mark.parametrize("case,reason", [("unknown", "docker-discovery-incomplete"), ("missing-image", "immutable-image-unavailable"),
    ("oci", "oci-mapping-unverified"), ("volume", "resource-ownership-conflict"), ("network", "resource-ownership-conflict"),
    ("proxy", "required-proxy-network-unavailable-or-foreign"), ("alias", "proxy-alias-conflict"),
    ("container", "container-ownership-conflict"), ("anonymous", "image-anonymous-volume-forbidden")])
def test_resource_preflight(app, case, reason):
    artifact, docker = compose.render_compose(app, test_session=SESSION), docker_state(app)
    if case == "unknown":
        docker = replace(docker, complete=False)
    elif case == "missing-image":
        docker = replace(docker, images=())
    elif case == "oci":
        docker = replace(docker, images=tuple(replace(i, verified=False) for i in docker.images))
    elif case == "anonymous":
        docker = replace(docker, images=tuple(replace(i, defaults_json=encode({"Volumes": {"/undeclared": {}}})) for i in docker.images))
    elif case in ("volume", "network", "proxy"):
        records = list(docker.resources)
        i = next(i for i, r in enumerate(records) if r.kind == ("network" if case == "proxy" else case) and
                 (case != "proxy" or r.name.endswith("-proxy")))
        f = records[i].facts
        f["labels"] = {}
        records[i] = replace(records[i], facts_json=encode(f))
        docker = replace(docker, resources=tuple(records))
    else:
        fake = resource("container", artifact.project + "-foreign-1" if case == "container" else "unrelated", {},
                        networks={artifact.model["networks"]["proxy"]["name"]: {"Aliases": [f"{SESSION}-{app.id}-frontend"]}}, mounts=[])
        docker = replace(docker, resources=docker.resources + (fake,))
    assert reason in deployment.preflight_resources(app, artifact, docker)


def test_refs_and_platform_block(root):
    put(root)
    app = build_desired_state(root, SHA).applications[0]
    assert "runtime-reference-contract-unavailable" in deployment.preflight_resources(app, compose.render_compose(app, test_session=SESSION), docker_state(app))
    spec = app.spec
    spec["components"][0]["platform"] = "linux/arm64"
    app = replace(app, spec_json=encode(spec))
    assert "unsupported-platform" in deployment.preflight_resources(app, compose.render_compose(app, test_session=SESSION), docker_state(app))


def test_freshness_and_lock(app, tmp_path):
    d, o = desired(app), load_observed_state(empty())
    plan = build_reconciliation_plan(d, o)
    lock = tmp_path / "lock"
    adapter = FakeAdapter()
    def verify_locked():
        with pytest.raises(DockerError, match="host-lock-busy"):
            with deployment.host_lock(lock):
                pass
    provider = Provider(d, o, docker_state(app), verify_locked)
    results = deployment.execute(d, o, plan, app.id, adapter, provider, lock_directory=lock)
    assert provider.calls == 1 and results[0].status == "applied-unverified"
    for fd, fo in [(replace(d, git_revision="b" * 40), o), (d, load_observed_state(observation(d)))]:
        adapter = FakeAdapter()
        results = deployment.execute(d, o, plan, app.id, adapter, Provider(fd, fo, docker_state(app)), lock_directory=lock)
        assert {r.status for r in results} == {"blocked"} and not adapter.calls
    with deployment.host_lock(lock):
        results = deployment.execute(d, o, plan, app.id, adapter, provider, lock_directory=lock)
        assert results[0].reasons == ("host-lock-busy",)
    assert (lock / "host.lock").exists()
    with pytest.raises(DockerError, match="stale-plan"):
        deployment.deployment_preview(replace(d, git_revision="c" * 40), o, plan, app.id)


def test_image_identity_requires_manifest_evidence():
    ref = "ghcr.io/fixtures/test@sha256:" + "a" * 64
    doc = {"Id": "sha256:" + "c" * 64, "Os": "linux", "Architecture": "amd64", "RepoDigests": [ref], "Config": {}}
    assert not docker_discovery.image_evidence(ref, doc).verified
    doc["Descriptor"] = {"digest": ref.split("@")[1], "mediaType": "application/vnd.oci.image.manifest.v1+json"}
    assert docker_discovery.image_evidence(ref, doc).verified
    doc["Descriptor"]["mediaType"] = "application/vnd.oci.image.index.v1+json"
    assert not docker_discovery.image_evidence(ref, doc).verified
    doc["Descriptor"]["digest"] = doc["Id"]
    assert not docker_discovery.image_evidence(ref, doc).verified


def test_discovery_is_incomplete_registry_and_unknown_not_absent():
    class Adapter:
        def inventory(self, kind):
            raise DockerError("denied")
    result = docker_discovery.discover(Adapter(), ())
    assert not result.complete
    o = result.registry_observed()
    assert o.document["scope"] == {"kind": "registry", "complete": False, "trusted": False}
    assert build_reconciliation_plan(DesiredState(SHA, ()), o).document["status"] == "blocked"


def test_command_adapter_selection_no_shell_and_sanitized_environment(app, tmp_path, monkeypatch):
    path = tmp_path / "docker.sock"
    monkeypatch.setattr(docker_adapter, "verify_socket", lambda endpoint: str(path))
    calls = []
    monkeypatch.setattr(docker_adapter.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(docker_adapter, "run_bounded", lambda argv, **kw: calls.append((argv, kw)) or b"")
    monkeypatch.setenv("DOCKER_HOST", "ssh://production")
    adapter = docker_adapter.LocalDockerAdapter(docker_adapter.LocalScope("unix://" + str(path), SESSION, True))
    artifact = compose.render_compose(app, test_session=SESSION)
    monkeypatch.setattr(adapter, "prerequisites", lambda: None)
    adapter.up(artifact, "backend")
    argv, kw = calls[0]
    assert argv[-7:] == ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "backend"]
    assert argv[1:3] == ["--host", "unix://" + str(path)]
    assert "--project-name" in argv and "--file" in argv and kw["timeout"] == 60
    assert not set(argv) & {"down", "--remove-orphans", "--build", "prune", "pull"}
    with pytest.raises(DockerError, match="wrong-test-project"):
        adapter.up(compose.render_compose(app), "backend")


def test_subprocess_bounds_redaction_and_environment(monkeypatch):
    real = subprocess.Popen
    calls = []
    def capture(*args, **kw):
        calls.append(kw)
        return real(*args, **kw)
    monkeypatch.setattr(docker_adapter.subprocess, "Popen", capture)
    monkeypatch.setenv("DOCKER_CONTEXT", "production")
    assert docker_adapter.run_bounded([sys.executable, "-c", "print('ok')"]).strip() == b"ok"
    assert calls[0]["shell"] is False and "DOCKER_CONTEXT" not in calls[0]["env"]
    for code, expected in [("import time; time.sleep(1)", "docker-timeout"),
                           ("print('secret'); raise SystemExit(1)", "docker-command-failed"),
                           ("print('x'*2000000)", "docker-output-limit")]:
        with pytest.raises(DockerError, match=expected) as error:
            docker_adapter.run_bounded([sys.executable, "-c", code], timeout=0.1)
        assert "secret" not in str(error.value)
    with pytest.raises(DockerError) as error:
        docker_adapter.run_bounded([sys.executable, "-c", "import sys; sys.stderr.write('connection refused'); sys.exit(1)"])
    assert error.value.unknown


@pytest.mark.parametrize("endpoint", ["ssh://host", "tcp://127.0.0.1:2375", "unix://relative", "", "default"])
def test_remote_endpoints_rejected(endpoint):
    with pytest.raises(DockerError):
        docker_adapter.LocalScope(endpoint, SESSION, True).validate()


def test_official_missing_and_smoke_optin(monkeypatch, capsys):
    monkeypatch.setattr(docker_adapter.shutil, "which", lambda _: None)
    assert docker_adapter.official_validate(None)["status"] == "not-performed"
    assert local_docker_smoke.main([]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "not-performed"


def test_smoke_namespace_and_cleanup_ownership(app):
    app = local_docker_smoke.test_application(app.spec["components"][0]["image"])
    artifact = compose.render_compose(app, test_session=SESSION)
    state = docker_state(app, True)
    targets = local_docker_smoke.verify_cleanup(state, [artifact], SESSION, set())
    # Proxy is separately provisioned; smoke uses internal apps only, no proxy lifecycle.
    assert targets
    baseline = {(targets[0].kind, targets[0].identifier)}
    with pytest.raises(DockerError, match="predates-session"):
        local_docker_smoke.verify_cleanup(state, [artifact], SESSION, baseline)
    foreign = resource("volume", artifact.project + "-foreign", {})
    with pytest.raises(DockerError, match="cleanup-ownership-conflict"):
        local_docker_smoke.verify_cleanup(replace(state, resources=state.resources + (foreign,)), [artifact], SESSION, set())
    with pytest.raises(Exception, match="invalid-test-session"):
        compose.render_compose(app, test_session="portfolio")


def test_smoke_missing_images_not_performed(monkeypatch):
    monkeypatch.setattr(local_docker_smoke, "discover", lambda *args: docker_discovery.DockerObservation((), (), True))
    class Adapter:
        test_session = SESSION
        def prerequisites(self):
            pass
        def up(self, *args):
            pytest.fail("mutation without images")
    report = local_docker_smoke.smoke(Adapter(), "ghcr.io/fixtures/a@sha256:" + "a" * 64, "ghcr.io/fixtures/a@sha256:" + "b" * 64)
    assert report["status"] == "not-performed"


def test_smoke_cleanup_conflict_makes_no_commands(app, monkeypatch):
    a = compose.render_compose(app, test_session=SESSION)
    bad = resource("volume", a.project + "-foreign", {})
    state = replace(docker_state(app), resources=(bad,))
    monkeypatch.setattr(local_docker_smoke, "discover", lambda *args: state)
    class Adapter:
        test_session = SESSION
        def _run(self, *args):
            pytest.fail("unsafe cleanup")
    with pytest.raises(DockerError, match="cleanup-ownership-conflict"):
        local_docker_smoke.cleanup(Adapter(), [a], set())


def test_cli_and_make_interfaces(root, app, capsys):
    args = ["--root", str(root), "--application", app.id, "--git-revision", SHA]
    assert deployment_cli.main(["compose-render", *args]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == f"portfolio-{app.id}"
    snapshot = root / "observed.json"
    snapshot.write_text(encode(empty()))
    assert deployment_cli.main(["deployment-plan", *args, "--observed", str(snapshot)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["authorization"] and report["operations"]
    assert all(o["eligibility"] == "requires-checks" and not o["reasons"] for o in report["operations"])
    env_args = ["make", "--silent", "compose-render", f"PYTHON={sys.executable}", f"REGISTRY_ROOT={root}",
                f"APPLICATION={app.id}", f"GIT_REVISION={SHA}"]
    result = subprocess.run(env_args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["services"]
    dry = subprocess.run(["make", "-n", "check"], capture_output=True, text=True, timeout=10)
    assert "local_docker_smoke" not in dry.stdout and "docker " not in dry.stdout


def test_public_mock_adapter_is_offline(app, tmp_path):
    adapter = docker_adapter.MockDockerAdapter(SESSION)
    results, _ = apply(app, tmp_path, adapter=adapter)
    assert results[0].status == "applied-unverified"
    assert [call[1] for call in adapter.calls] == ["backend", "worker"]


@pytest.mark.parametrize("version", [b"2.40.0", b"v2.40.3", b"2.40.3+ds1-0ubuntu1~24.04.1"])
def test_official_syntax_validation_uses_no_daemon(app, monkeypatch, version):
    calls = []
    monkeypatch.setattr(docker_adapter.shutil, "which", lambda _: "/usr/bin/docker")
    def run(argv, **kwargs):
        calls.append(argv)
        return version if "version" in argv else b""
    monkeypatch.setattr(docker_adapter, "run_bounded", run)
    assert docker_adapter.official_validate(compose.render_compose(app))["status"] == "passed"
    assert calls[-1][-2:] == ["config", "-q"]
    assert "unix:///nonexistent/pd-no-daemon.sock" in calls[-1]
    monkeypatch.setattr(docker_adapter, "run_bounded", lambda *a, **k: b"")
    assert docker_adapter.official_validate(compose.render_compose(app))["status"] == "not-performed"


@pytest.mark.parametrize("version,accepted", [(b"2.40.3+ds1-0ubuntu1~24.04.1", True),
                                               (b"v2.40.3", True), (b"1.29.2", False),
                                               (b"3.0.0", False), (b"2.40.3\nuntrusted", False)])
def test_real_adapter_compose_version_accepts_ubuntu_and_requires_v2(monkeypatch, version, accepted):
    adapter = object.__new__(docker_adapter.LocalDockerAdapter)
    calls = []
    def run(args):
        calls.append(args)
        return version if args == ["compose", "version", "--short"] else b'{"Os":"linux","Arch":"amd64"}'
    monkeypatch.setattr(adapter, "_run", run)
    if accepted:
        adapter.prerequisites()
        assert len(calls) == 2
    else:
        with pytest.raises(DockerError, match="compose-v2-required"):
            adapter.prerequisites()
        assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["duplicate", "network-membership", "mount", "image-id", "command", "environment", "ports", "memory"])
def test_actual_runtime_overrides_block_noop(app, tmp_path, mutation):
    state = docker_state(app, True)
    records = list(state.resources)
    index = next(i for i, r in enumerate(records) if r.kind == "container")
    facts = records[index].facts
    if mutation == "duplicate":
        records.append(records[index])
    else:
        if mutation == "network-membership":
            facts["networks"]["foreign"] = {}
        elif mutation == "mount":
            facts["mounts"].append({"Type": "bind", "Source": "/host", "Destination": "/data"})
        elif mutation == "image-id":
            facts["imageId"] = "sha256:other"
        elif mutation == "command":
            facts["config"]["Cmd"] = ["unexpected"]
        elif mutation == "environment":
            facts["config"]["Env"] = ["SECRET=never-print"]
        elif mutation == "ports":
            facts["host"]["PortBindings"] = {"80/tcp": [{"HostPort": "8080"}]}
        elif mutation == "memory":
            facts["host"]["Memory"] = 1024
        records[index] = replace(records[index], facts_json=encode(facts))
    results, adapter = apply(app, tmp_path, observed=observation(desired(app)), docker=replace(state, resources=tuple(records)))
    assert {r.status for r in results} == {"blocked"} and not adapter.calls
    assert "never-print" not in encode([r.document for r in results])


def test_actual_runtime_accepts_typed_empty_image_defaults(app, tmp_path):
    state = docker_state(app, True)
    empty_defaults = {"Cmd": [], "Entrypoint": [], "Env": [], "ExposedPorts": {}, "Volumes": {}}
    artifact = compose.render_compose(app, test_session=SESSION)
    images = {item.reference: replace(item, defaults_json=encode(empty_defaults)) for item in state.images}
    for item in state.resources:
        if item.kind != "container":
            continue
        service = item.facts["labels"]["com.docker.compose.service"]
        assert docker_discovery.container_runtime(item, artifact, service, images[artifact.model["services"][service]["image"]])


def test_local_provider_never_promotes_docker_to_full_snapshot(root, app, monkeypatch):
    monkeypatch.setattr(docker_discovery, "discover", lambda *a: docker_state(app, True))
    d, o, state = docker_discovery.LocalStateProvider(root, SHA, object()).refresh()
    assert state.complete and not o.document["scope"]["complete"]
    assert build_reconciliation_plan(d, o).document["status"] == "blocked"


def test_lock_symlinks_and_unsafe_permissions(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(DockerError, match="unsafe-lock-directory"):
        with deployment.host_lock(unsafe):
            pass
    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    (safe / "host.lock").symlink_to(tmp_path / "other")
    with pytest.raises(DockerError, match="host-lock-unavailable"):
        with deployment.host_lock(safe):
            pass


def test_endpoint_socket_checks_do_not_connect(tmp_path, monkeypatch):
    with pytest.raises(DockerError, match="invalid-local-socket"):
        docker_adapter.verify_socket("unix://" + str(tmp_path))
    with pytest.raises(DockerError):
        docker_adapter.LocalScope("unix:///nonexistent/socket", SESSION, True).validate()
    with pytest.raises(DockerError):
        docker_adapter.LocalScope("unix:///nonexistent/socket", "production", True).validate()


def test_cleanup_orders_only_owned_session_resources(app, monkeypatch):
    app = local_docker_smoke.test_application(app.spec["components"][0]["image"])
    artifact = compose.render_compose(app, test_session=SESSION)
    resources = list(docker_state(app, True).resources)
    calls = []
    class Adapter:
        test_session = SESSION
        def _run(self, args, **kw):
            calls.append(args)
            if args[1] == "rm":
                resources[:] = [r for r in resources if r.identifier != args[2] and r.name != args[2]]
    monkeypatch.setattr(local_docker_smoke, "discover", lambda *a: docker_discovery.DockerObservation(tuple(resources), (), True))
    local_docker_smoke.cleanup(Adapter(), [artifact], set())
    assert not resources
    assert all(args[:2] in (["container", "stop"], ["container", "rm"], ["network", "rm"], ["volume", "rm"]) for args in calls)
    assert not any("-f" in args or "prune" in args for args in calls)


def test_smoke_noop_uses_inspected_runtime(app, monkeypatch):
    app = local_docker_smoke.test_application(app.spec["components"][0]["image"])
    artifact = compose.render_compose(app, test_session=SESSION)
    monkeypatch.setattr(local_docker_smoke, "discover", lambda *a: docker_state(app, True))
    adapter = FakeAdapter()
    assert local_docker_smoke.smoke_apply(adapter, app, artifact, (), "backend") == "no-op"
    assert not adapter.calls


def test_unexpected_mutation_error_is_unknown(app, tmp_path):
    adapter = FakeAdapter(RuntimeError("sensitive diagnostic"))
    results, _ = apply(app, tmp_path, adapter=adapter)
    assert results[0].status == "unknown" and results[1].status == "blocked"
    assert "sensitive" not in encode([r.document for r in results])


def test_fresh_admission_error_blocks_before_mutation(app, tmp_path):
    class Broken:
        def refresh(self):
            raise OSError("sensitive path")
    results, adapter = apply(app, tmp_path, provider=Broken())
    assert results[0].reasons == ("fresh-state-unavailable",) and not adapter.calls


def test_cleanup_accepts_updated_runtime_label(app):
    app = local_docker_smoke.test_application(app.spec["components"][0]["image"])
    initial = compose.render_compose(app, test_session=SESSION)
    spec = app.spec
    spec["components"][0]["image"] = "ghcr.io/fixtures/next@sha256:" + "b" * 64
    updated = replace(app, spec_json=encode(spec))
    # Runtime label is mutable execution metadata, not the stable ownership contract.
    assert local_docker_smoke.verify_cleanup(docker_state(updated, True), [initial], SESSION, set())


def test_full_smoke_with_stateful_fake(monkeypatch):
    image_a, image_b = ("ghcr.io/fixtures/test@sha256:" + c * 64 for c in ("a", "b"))
    resources, calls = {}, []
    counter = [0]
    class Adapter:
        test_session = SESSION
        def prerequisites(self):
            pass
        def authorize(self, artifact):
            assert artifact.project.startswith("pd-smoke-" + SESSION)
        def up(self, artifact, service):
            calls.append((artifact.application, service))
            counter[0] += 1
            app = local_docker_smoke.test_application(image_a, artifact.application)
            spec = app.spec
            for c in spec["components"]:
                c["image"] = artifact.model["services"][c["name"]]["image"]
            state = docker_state(replace(app, spec_json=encode(spec)), True)
            for r in state.resources:
                if r.kind != "container" or r.facts["labels"]["com.docker.compose.service"] == service:
                    if r.kind == "container":
                        r = replace(r, identifier=f"id{counter[0]}")
                    resources[(r.kind, r.name)] = r
        def inspect(self, kind, name):
            raise DockerError("docker-command-failed")
        def _run(self, args, **kwargs):
            if args[1] == "rm":
                resources_copy = list(resources.items())
                for key, r in resources_copy:
                    if r.identifier == args[2] or r.name == args[2]:
                        del resources[key]
    def observe(*args):
        return docker_discovery.DockerObservation(tuple(resources.values()), (image(image_a), image(image_b)), True)
    monkeypatch.setattr(local_docker_smoke, "discover", observe)
    report = local_docker_smoke.smoke(Adapter(), image_a, image_b)
    assert report["status"] == "passed", report
    assert report["cleanup"] == "completed" and not resources
    assert calls.count(("sample", "backend")) == 2 and calls.count(("sample", "frontend")) == 1


def set_backend_image(root, app, reference):
    path = root / f"registry/applications/{app.id}.yaml"
    document = yaml.safe_load(path.read_text())
    next(c for c in document["spec"]["components"] if c["name"] == "backend")["image"] = reference
    path.write_text(yaml.safe_dump(document))


@pytest.mark.parametrize("case", ["verified", "missing-old", "wrong-mapping", "index", "missing-reference", "no-previous"])
def test_previous_image_discovery_and_update(root, app, tmp_path, case):
    old = next(c for c in app.spec["components"] if c["name"] == "backend")["image"]
    new = "ghcr.io/fixtures/backend@sha256:" + "b" * 64
    set_backend_image(root, app, new)
    previous = observation(desired(app))
    if case == "missing-reference":
        next(c for c in previous["applications"][0]["components"] if c["name"] == "backend")["imageIdentity"] = None
    observed = load_observed_state(previous)

    class InspectionAdapter:
        def __init__(self):
            self.references = []

        def inventory(self, kind):
            return []

        def inspect(self, kind, reference):
            assert kind == "image"
            self.references.append(reference)
            if reference == old and case == "missing-old":
                raise DockerError("missing")
            descriptor = {"digest": reference.split("@")[1],
                          "mediaType": "application/vnd.oci.image.manifest.v1+json"}
            if reference == old and case == "wrong-mapping":
                descriptor["digest"] = new.split("@")[1]
            if reference == old and case == "index":
                descriptor["mediaType"] = "application/vnd.oci.image.index.v1+json"
            return {"Id": image(reference).image_id, "Os": "linux", "Architecture": "amd64",
                    "RepoDigests": [reference], "Descriptor": descriptor, "Config": {}}

    inspector = InspectionAdapter()
    d, limited, discovered = docker_discovery.LocalStateProvider(
        root, SHA, inspector, previous_observed=None if case == "no-previous" else observed).refresh()
    assert new in inspector.references
    assert (old in inspector.references) == (case not in ("missing-reference", "no-previous"))
    assert len(inspector.references) == len(set(inspector.references))
    assert limited.document["scope"] == {"kind": "registry", "complete": False, "trusted": False}
    # Full observations and runtime resources here are explicitly synthetic;
    # the local provider itself remains unable to authorize execution.
    state = replace(discovered, resources=docker_state(app, True).resources)
    adapter = FakeAdapter()
    results = deployment.execute(d, observed, build_reconciliation_plan(d, observed), app.id,
                                 adapter, Provider(d, observed, state), lock_directory=tmp_path / "lock")
    if case == "verified":
        assert [call[1] for call in adapter.calls] == ["backend"]
        assert sum(r.status == "applied-unverified" for r in results) == 1
    else:
        assert not adapter.calls and {r.status for r in results} == {"blocked"}
        reason = "reconciliation-not-admissible" if case == "missing-reference" else "effective-runtime-not-established"
        assert all(reason in r.reasons for r in results)


@pytest.mark.parametrize("case,reason", [
    ("update-health", "health-unobserved"),
    ("update-storage", "storage-change-requires-lifecycle-policy"),
    ("create-storage", "storage-change-requires-lifecycle-policy"),
    ("unknown", "unknown-state-or-ownership-conflict"),
    ("incomplete", "incomplete-or-untrusted-snapshot"),
])
def test_blocked_preview_preserves_proposal(root, app, capsys, case, reason):
    previous = observation(desired(app))
    if case.startswith("update"):
        set_backend_image(root, app, "ghcr.io/fixtures/backend@sha256:" + "b" * 64)
    actual = previous["applications"][0]
    backend = next(c for c in actual["components"] if c["name"] == "backend")
    if case == "update-health":
        backend["health"] = "unknown"
    elif case.endswith("storage"):
        actual["storage"] = {"type": "stateless"}
        if case == "create-storage":
            actual["components"].remove(backend)
    elif case == "unknown":
        backend.update(existence="unknown", runtime=None, imageIdentity=None)
    else:
        previous["scope"]["complete"] = False
    snapshot = root / "observed.json"
    snapshot.write_text(encode(previous))
    assert deployment_cli.main(["deployment-plan", "--root", str(root), "--application", app.id,
                                "--git-revision", SHA, "--observed", str(snapshot)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["authorization"] and not report["executed"]
    for op in report["operations"]:
        assert op["eligibility"] == "blocked"
        assert reason in op["reasons"] and "reconciliation-not-admissible" in op["reasons"]
    proposal = next(op for op in report["operations"] if op["component"] == "backend")
    if case.startswith("update"):
        assert proposal["action"] == "update-component"
    elif case.startswith("create"):
        assert proposal["action"] == "create"
