"""Stage 07 OFFLINE contracts. Engine/HTTP/Multipass are fakes, never real E2E."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts.compose import LABEL, render_compose
from scripts.deployment import execute
from scripts.deployment_journal import locked_journal
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import discover
from scripts.e2e import safety, transports, gateway, vm, observations, runner, cleanup
from scripts.e2e.fixtures import create_registry, change_image
from scripts.e2e.images import Images, ROLES
from scripts.planning_inputs import build_desired_state
from scripts.reconciliation import build_reconciliation_plan
from test_deployment import SESSION, resource, docker_state

SHA = "a" * 40


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline tests forbid VM/Docker/Caddy/network/process operations")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(runner.os, "fork", forbidden)


@pytest.fixture
def images(tmp_path):
    records = {}
    for index, role in enumerate(sorted(ROLES)):
        manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": "sha256:" + str(index + 1) * 64,
                               "mediaType": "application/vnd.oci.image.config.v1+json", "size": 128}, "layers": []}
        raw = json.dumps(manifest).encode()
        (tmp_path / f"{role}.json").write_bytes(raw)
        records[role] = {"reference": f"ghcr.io/fixtures/pd-e2e-{role}@sha256:" + sha256(raw).hexdigest(),
                         "manifest": f"{role}.json"}
    path = tmp_path / "lock.json"
    path.write_text(json.dumps({"version": 1, "images": records}))
    return Images(path)


@pytest.fixture
def registry(tmp_path, images):
    root = tmp_path / "registry-root"
    create_registry(root, images)
    return root


class Engine:
    """Synthetic Engine inspect documents, with actual Stage 07 readers exercised."""
    test_session = SESSION

    def __init__(self, root, images):
        self.root, self.images = root, images
        self.present, self.up_calls = {}, []
        self.guest = SimpleNamespace(validate=lambda: None)
        self.generation = 0

    def budget(self, timeout):
        return nullcontext()

    def authorize(self, artifact):
        assert artifact.project.startswith(f"pd-smoke-{SESSION}-")

    def up(self, artifact, service):
        self.authorize(artifact)
        self.up_calls.append((artifact.project, service))
        self.generation += 1
        self.present[(artifact.application, service)] = (artifact.model, self.generation)

    def image(self, ref):
        return {"Id": self.images.manifests[ref]["config"]["digest"], "Os": "linux", "Architecture": "amd64",
                "RepoDigests": [ref], "Config": {"User": "10001", "Healthcheck": {"Test": ["CMD", "check"]}}}

    def resources(self):
        containers, networks, volumes = [], {}, {}
        proxy = f"pd-smoke-{SESSION}-proxy"
        networks[proxy] = {"Id": proxy, "Name": proxy, "Driver": "bridge", "Internal": True, "Scope": "local",
                          "Options": {}, "Containers": {}, "Labels": {f"{LABEL}.managed": "true", f"{LABEL}.role": "proxy",
                                                                       f"{LABEL}.test-session": SESSION}}
        for (app, service), (model, version) in self.present.items():
            conf = model["services"][service]
            identity, name = f"{app}-{service}-{version}", f"{model['name']}-{service}-1"
            attachments = {}
            for key, opts in conf["networks"].items():
                net = model["networks"][key]
                if net["name"] not in networks:
                    networks[net["name"]] = {"Id": net["name"], "Name": net["name"], "Driver": "bridge",
                        "Internal": True, "Scope": "local", "Options": {}, "Containers": {},
                        "IPAM": {"Config": [{"Subnet": "172.28.0.0/16"}]},
                        "Labels": {**net["labels"], "com.docker.compose.project": model["name"], "com.docker.compose.network": key}}
                ip = f"172.28.0.{version + 2}"
                attachments[net["name"]] = {"NetworkID": net["name"], "IPAddress": ip,
                                           "Aliases": [service, *opts.get("aliases", [])]}
                networks[net["name"]]["Containers"][identity] = {"Name": name, "IPv4Address": ip + "/16"}
            mounts = []
            for mount in conf.get("volumes", []):
                vol = model["volumes"][mount["source"]]
                volumes[vol["name"]] = {"Name": vol["name"], "Driver": "local", "Scope": "local", "Options": {},
                    "Labels": {**vol["labels"], "com.docker.compose.project": model["name"], "com.docker.compose.volume": mount["source"]}}
                mounts.append({"Type": "volume", "Name": vol["name"], "Destination": mount["target"], "RW": True})
            defaults = self.image(conf["image"])
            containers.append({"Id": identity, "Name": "/" + name, "Image": defaults["Id"],
                "Config": {**defaults["Config"], "Image": conf["image"], "Labels": {**conf["labels"],
                    "com.docker.compose.project": model["name"], "com.docker.compose.service": service}},
                "HostConfig": {"NetworkMode": model["networks"]["private"]["name"]},
                "State": {"Status": "running", "Running": True, "Health": {"Status": "healthy"}},
                "NetworkSettings": {"Networks": attachments}, "Mounts": mounts})
        return {"container": containers, "network": list(networks.values()), "volume": list(volumes.values())}

    def inventory(self, kind):
        return tuple(self.resources()[kind])

    def inspect(self, kind, identity):
        if kind == "image":
            return self.image(identity)
        return next(r for r in self.resources()[kind] if r.get("Id", r["Name"]) == identity)


class ActiveGateway:
    def __init__(self):
        self.routes, self.calls = {}, []

    def observations(self, desired, endpoints):
        return deepcopy(self.routes), True

    def activate(self, desired, app, endpoints, ready, *, lease):
        assert lease.active
        assert ready and (app, "frontend") in endpoints
        self.calls.append(app)
        self.routes[app] = next(a.spec["ingress"] for a in desired.applications if a.id == app)


@pytest.fixture
def world(registry, images, monkeypatch):
    adapter = Engine(registry, images)
    gate = ActiveGateway()
    monkeypatch.setattr(transports, "request", lambda ip, port, path, **kw: (200, b""))
    probes = transports.Probes(adapter)
    read = observations.Reader(registry, SHA, adapter, images, gate, probes)
    provider = read.provider()
    return SimpleNamespace(adapter=adapter, reader=read, provider=provider, gate=gate, probes=probes)


def test_image_identity_uses_manifest_config_relationship(images):
    ref = images.records["v1"]
    engine = Engine(None, images).image(ref)
    assert images.evidence(ref, engine).verified
    assert ref.split("@")[1] != engine["Id"]
    for altered in ({"Id": "sha256:" + "f" * 64}, {"RepoDigests": []}, {"Architecture": "arm64"}):
        assert not images.evidence(ref, {**engine, **altered}).verified


@pytest.mark.parametrize("change", ["digest", "index", "tag", "production", "path", "extra", "missing"])
def test_image_contract_rejections(images, tmp_path, change):
    path = tmp_path / "lock.json"
    doc = json.loads(path.read_text())
    rec = doc["images"]["v1"]
    if change == "digest":
        (tmp_path / rec["manifest"]).write_text("{}")
    elif change == "index":
        raw = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []}).encode()
        (tmp_path / rec["manifest"]).write_bytes(raw)
        rec["reference"] = rec["reference"].split("@")[0] + "@sha256:" + sha256(raw).hexdigest()
    elif change == "tag":
        rec["reference"] = "ghcr.io/fixtures/pd-e2e-v1:latest"
    elif change == "production":
        rec["reference"] = rec["reference"].replace("pd-e2e-v1", "application")
    elif change == "path":
        rec["manifest"] = "../v1.json"
    elif change == "extra":
        rec["trusted"] = True
    else:
        del doc["images"]["worker"]
    path.write_text(json.dumps(doc))
    with pytest.raises((DockerError, ValueError)):
        Images(path)


def test_separate_registry_admission_and_no_reuse(registry, images):
    desired = build_desired_state(registry, SHA)
    assert len(desired.applications) == 9
    assert all(a.id.startswith("e2e-") for a in desired.applications)
    assert yaml.safe_load(Path("policy/admission.yaml").read_text())["applications"] == []
    with pytest.raises(FileExistsError):
        create_registry(registry, images)
    change_image(registry, "e2e-app", images.records["v2"])
    with pytest.raises(Exception):
        change_image(registry, "e2e-app", "ghcr.io/foreign/image@sha256:" + "1" * 64)


def test_complete_provider_and_real_journal_with_simulated_transports(world, registry, images, tmp_path):
    """Offline integration of actual contracts; this is explicitly simulated."""
    def apply():
        desired, observed, _ = world.provider.refresh()
        lifecycle = runner.E2ELifecycle(tmp_path / "journal", world.probes, policy=runner.POLICY,
            side_effects_excluded=True, sleep=lambda _: None)
        return execute(desired, observed, build_reconciliation_plan(desired, observed), "e2e-app",
                       world.adapter, world.provider, lifecycle=lifecycle, lock_directory=tmp_path / "lock")
    result = apply()
    assert all(r.release_success for r in result)
    assert len(world.adapter.up_calls) == 3 and world.gate.calls
    before = {k: v[1] for k, v in world.adapter.present.items()}
    change_image(registry, "e2e-app", images.records["v2"])
    result = apply()
    assert all(r.release_success for r in result)
    assert len(world.adapter.up_calls) == 4
    assert world.adapter.present[("e2e-app", "frontend")][1] == before[("e2e-app", "frontend")]
    with locked_journal(tmp_path / "journal", lock_directory=tmp_path / "lock") as j:
        good = j.last_good("e2e-app", "backend")
        assert good["target"] == images.records["v2"]
    change_image(registry, "e2e-app", images.records["bad"])
    def health(ip, port, path, **kw):
        bad_ids = [v[1] for k, v in world.adapter.present.items()
                       if v[0]["services"][k[1]]["image"] == images.records["bad"]]
        return (503 if any(ip == f"172.28.0.{i+2}" for i in bad_ids) else 200), b""
    # Both sampling and bounded Stage06 readiness see the failed real-adapter result.
    from unittest.mock import patch
    with patch.object(transports, "request", health):
        result = apply()
    assert any(r.status == "rolled-back-verified" for r in result), [(r.status, r.reasons) for r in result]
    with locked_journal(tmp_path / "journal", lock_directory=tmp_path / "lock") as j:
        assert j.last_good("e2e-app", "backend") == good
        assert not j.incomplete()


def test_provider_rejects_unverified_image_or_incomplete_inventory(world, monkeypatch):
    monkeypatch.setattr(world.adapter, "image", lambda ref: {"Id": "unverified"})
    with pytest.raises(DockerError):
        world.provider.refresh()
    assert world.provider.sample is None


def test_provider_rejects_runtime_drift_and_foreign_owner(world, registry, monkeypatch):
    app = build_desired_state(registry, SHA).applications[0]
    world.adapter.up(render_compose(app, test_session=SESSION), "backend")
    original = world.adapter.resources
    def drift():
        raw = original()
        raw["container"][0]["HostConfig"]["Privileged"] = True
        return raw
    monkeypatch.setattr(world.adapter, "resources", drift)
    with pytest.raises(DockerError, match="effective-test-runtime-unverified"):
        world.provider.refresh()


def test_probe_pins_id_no_redirect_worker_uses_engine(world, registry, monkeypatch):
    app = build_desired_state(registry, SHA).applications[0]
    artifact = render_compose(app, test_session=SESSION)
    world.adapter.up(artifact, "backend")
    component = next(c for c in app.spec["components"] if c["name"] == "backend")
    identity = world.adapter.inventory("container")[0]["Id"]
    monkeypatch.setattr(transports, "request", lambda *a, **kw: (302, b""))
    assert world.probes.probe(component, identity, timeout=2, follow_redirects=False).redirected
    with pytest.raises(DockerError):
        world.probes.probe(component, identity, timeout=2, follow_redirects=True)
    world.adapter.up(artifact, "worker")
    worker = next(c for c in app.spec["components"] if c["name"] == "worker")
    raw = world.adapter.inventory("container")[-1]
    monkeypatch.setattr(transports, "request", lambda *a, **kw: pytest.fail("worker must not use HTTP"))
    assert world.probes.probe(worker, raw["Id"], timeout=2, follow_redirects=False).health == "healthy"


def test_http_has_hard_subprocess_budget_no_proxy_or_dns(monkeypatch):
    calls = []
    def bounded(argv, **kw):
        calls.append((argv, kw))
        return b'[204, ""]'
    monkeypatch.setattr(transports, "run_bounded", bounded)
    assert transports.request("172.28.0.3", 8080, "/health", timeout=0.3) == (204, b"")
    assert calls[0][1]["timeout"] == 0.3
    with pytest.raises(ValueError):
        transports.request("production.example.com", 80, "/")


def test_vm_opt_in_before_commands_and_no_adoption():
    calls = []
    def call(argv, **kw):
        calls.append(argv)
        return json.dumps({"list": [{"name": f"pd-e2e-{SESSION}"}]}).encode()
    machine = vm.VM(SESSION, run=call)
    with pytest.raises(DockerError, match="authorization"):
        machine.prepare("")
    assert not calls
    with pytest.raises(DockerError, match="vm-already-exists"):
        machine.prepare(f"prepare:{SESSION}")
    assert len(calls) == 1 and calls[0][1] == "list"
    with pytest.raises(DockerError):
        vm.VM("production")


def test_vm_unknown_and_mounts_block_before_exec():
    for doc in ({"info": {}}, {"info": {f"pd-e2e-{SESSION}": {"state": "Running", "mounts": {"/host": {}}}}}):
        calls = []
        def call(argv, **kw):
            calls.append(argv)
            return json.dumps(doc).encode()
        with pytest.raises(DockerError):
            vm.VM(SESSION, run=call).guest("touch", "/mutation")
        assert len(calls) == 1


def test_vm_cloud_init_masks_public_caddy_and_has_no_host_mounts():
    cloud = vm.cloud_init(SESSION)
    assert cloud["hostname"] == f"pd-e2e-{SESSION}"
    assert cloud["runcmd"][0] == ["systemctl", "mask", "caddy.service"]
    config = json.loads(next(f["content"] for f in cloud["write_files"] if f["path"].endswith("caddy.json")))
    assert config == gateway.configuration()
    assert config["admin"]["listen"] == "127.0.0.1:2019"
    assert "mounts" not in cloud and "docker" not in str(cloud.get("groups", []))


@pytest.mark.parametrize("action", ["prepare", "images", "test", "clean", "diagnose"])
def test_public_cli_missing_authorization_is_offline(action, capsys):
    assert vm.main([action, "--session", SESSION]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "blocked" and doc["realE2E"] == "not-performed"


def test_wrong_guest_authorization_rejected_before_host_files(monkeypatch):
    scope = safety.GuestScope(SESSION, "", "machine", "daemon")
    monkeypatch.setattr(safety.Path, "read_text", lambda *a: pytest.fail("must fail before host access"))
    with pytest.raises(DockerError, match="authorization"):
        scope.validate()


def test_private_state_rejects_links_and_public_permissions(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    with pytest.raises(DockerError):
        safety.private_json(path)
    path.chmod(0o600)
    assert safety.private_json(path) == {}
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(DockerError):
        safety.private_json(link)


def test_gateway_readiness_authorization_preserves_retained_routes(world, monkeypatch):
    scope = SimpleNamespace(session=SESSION, validate=lambda: None, directory=world.reader.root.parent)
    gate = gateway.Gateway(scope, authorization=f"activate:{SESSION}")
    desired, _, _ = world.provider.refresh()
    state, loads = [gateway.configuration()], []
    def http(ip, port, path, **kwargs):
        if path == "/config/":
            return 200, json.dumps(state[0]).encode()
        if path == "/load":
            state[0] = json.loads(kwargs["data"])
            loads.append(deepcopy(state[0]))
            return 200, b""
        return 200, b""
    monkeypatch.setattr(gateway, "request", http)
    monkeypatch.setattr(gate, "validate", lambda candidate: None)
    endpoints = {("e2e-app", "frontend"): "172.28.0.3", ("e2e-sentinel", "frontend"): "172.28.0.4"}
    with pytest.raises(DockerError):
        gate.activate(desired, "e2e-app", endpoints, False, lease=SimpleNamespace(active=True))
    assert not loads
    with pytest.raises(DockerError, match="host-lock"):
        gate.activate(desired, "e2e-app", endpoints, True)
    gate.activate(desired, "e2e-sentinel", endpoints, True, lease=SimpleNamespace(active=True))
    gate.activate(desired, "e2e-app", endpoints, True, lease=SimpleNamespace(active=True))
    assert {r["@id"] for r in state[0]["apps"]["http"]["servers"]["e2e"]["routes"]} == {"e2e-app", "e2e-sentinel"}
    assert not gate.activate(desired, "e2e-app", endpoints, True, lease=SimpleNamespace(active=True))
    assert len(loads) == 2
    monkeypatch.setattr(gate, "validate", lambda c: (_ for _ in ()).throw(DockerError("invalid-candidate")))
    before = deepcopy(state[0])
    with pytest.raises(DockerError):
        gate.activate(desired, "e2e-app", {**endpoints, ("e2e-app", "frontend"): "172.28.0.5"}, True, lease=SimpleNamespace(active=True))
    assert before == state[0] and len(loads) == 2


def test_gateway_foreign_config_cannot_be_loaded(monkeypatch):
    gate = gateway.Gateway(SimpleNamespace(validate=lambda: None))
    monkeypatch.setattr(gateway, "request", lambda *a, **kw: (200, b'{"admin":{"listen":"0.0.0.0:2019"}}'))
    with pytest.raises(DockerError, match="foreign-caddy"):
        gate.active()


def test_cleanup_preserves_volumes_baseline_and_foreign_attachments(registry, images):
    app = next(a for a in build_desired_state(registry, SHA).applications if a.id == "e2e-data")
    artifact = render_compose(app, test_session=SESSION)
    docker = docker_state(app, True)
    targets = cleanup.cleanup_plan(docker, [artifact], SESSION, set())
    assert targets and all(r.kind != "volume" for r in targets)
    with pytest.raises(DockerError):
        cleanup.cleanup_plan(docker, [artifact], SESSION, {(targets[0].kind, targets[0].identifier)})
    foreign = replace(docker, resources=(*docker.resources, resource("network", "pd-smoke-" + SESSION + "-foreign", {})))
    with pytest.raises(DockerError):
        cleanup.cleanup_plan(foreign, [artifact], SESSION, set())
    private = next(r for r in docker.resources if r.kind == "network")
    facts = private.facts
    facts["containers"] = {"foreign-id": {}}
    bad = replace(docker, resources=tuple(replace(r, facts_json=json.dumps(facts)) if r == private else r for r in docker.resources))
    with pytest.raises(DockerError):
        cleanup.cleanup_plan(bad, [artifact], SESSION, set())


def test_harness_stops_on_failure_reports_not_performed(tmp_path):
    obj = object.__new__(runner.Runner)
    obj.directory, obj.report = tmp_path, runner.Report(mode="offline")
    obj.bootstrap = lambda: None
    seen = []
    def scenario(name):
        seen.append(name)
        if len(seen) == 2:
            raise DockerError("injected-offline-failure")
        return {"simulated": True}
    obj.scenario = scenario
    doc = obj.run()
    assert doc["status"] == "failed" and doc["realE2E"] == "not-performed"
    assert [r["status"] for r in doc["scenarios"][:3]] == ["passed", "failed", "not-performed"]
    assert len(seen) == 2 and json.loads((tmp_path / "report.json").read_text()) == doc


def test_harness_interrupt_never_cleans_or_replays(tmp_path):
    obj = object.__new__(runner.Runner)
    obj.directory, obj.report = tmp_path, runner.Report(mode="offline")
    obj.bootstrap = lambda: None
    obj.scenario = lambda name: (_ for _ in ()).throw(KeyboardInterrupt())
    doc = obj.run()
    assert doc["status"] == "blocked" and doc["scenarios"][0]["status"] == "blocked"
    assert doc["realE2E"] == "not-performed" and len(doc["scenarios"]) == 15


def test_runtime_identity_ignores_image_driven_component_order(registry, images):
    from scripts.deployment_lifecycle import runtime_identity
    before = next(a for a in build_desired_state(registry, SHA).applications if a.id == "e2e-app")
    expected = runtime_identity(before, test_session=SESSION)
    for image in images.records.values():
        desired = change_image(registry, "e2e-app", image)
        app = next(a for a in desired.applications if a.id == "e2e-app")
        assert runtime_identity(app, test_session=SESSION) == expected


def test_vm_prepare_full_offline_orchestration(monkeypatch):
    calls = []
    def call(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "list":
            return b'{"list":[]}'
        if argv[1] == "info":
            return json.dumps({"info": {f"pd-e2e-{SESSION}": {"state": "Running", "mounts": {}}}}).encode()
        return b""
    monkeypatch.setattr(vm, "run_bounded", lambda *a, **kw: (SHA + "\n").encode())
    result = vm.VM(SESSION, run=call).prepare(f"prepare:{SESSION}")
    assert result["status"] == "passed"
    launch = next(c for c in calls if c[1] == "launch")
    assert "24.04" in launch and "--cloud-init" in launch
    assert any("pip" in " ".join(c) and "install" in c for c in calls)
    assert any("/etc/portfolio-e2e.json" in " ".join(c) for c in calls if c[1] == "exec")
    assert all(c[1] not in ("mount", "delete", "purge", "stop") for c in calls)


def test_vm_unknown_outcome_does_not_retry_launch():
    calls = []
    def call(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "list":
            return b'{"list":[]}'
        raise DockerError("unknown-launch", True)
    with pytest.raises(DockerError):
        vm.VM(SESSION, run=call).prepare(f"prepare:{SESSION}")
    assert [c[1] for c in calls] == ["list", "launch"]


def test_registry_read_failure_never_claims_complete(world, monkeypatch):
    monkeypatch.setattr(world.adapter, "inventory", lambda kind: (_ for _ in ()).throw(DockerError("missing-source")))
    with pytest.raises(DockerError):
        world.provider.refresh()
    assert world.provider.sample is None


def test_probe_network_foreign_or_wrong_ip_rejected(world, registry, monkeypatch):
    app = build_desired_state(registry, SHA).applications[0]
    world.adapter.up(render_compose(app, test_session=SESSION), "backend")
    raw = world.adapter.inventory("container")[0]
    original = world.adapter.inspect
    def foreign(kind, identity):
        doc = original(kind, identity)
        if kind == "network":
            doc["Labels"][f"{LABEL}.test-session"] = "foreign"
        return doc
    monkeypatch.setattr(world.adapter, "inspect", foreign)
    with pytest.raises(DockerError, match="network-ownership"):
        transports.container_address(raw, world.adapter)


def test_image_lock_builder_is_offline_and_exclusive(images, tmp_path):
    from scripts.e2e.image_lock import create
    repos = tmp_path / "repositories.json"
    repos.write_text(json.dumps({k: v.split("@")[0] for k, v in images.records.items()}))
    (tmp_path / "lock.json").unlink()  # temporary synthetic fixture, not runtime data
    rebuilt = create(repos, tmp_path)
    assert rebuilt.records == images.records
    with pytest.raises(FileExistsError):
        create(repos, tmp_path)


def test_report_bootstrap_blocked_has_no_real_e2e_claim(tmp_path):
    obj = object.__new__(runner.Runner)
    obj.directory, obj.report = tmp_path, runner.Report()
    obj.bootstrap = lambda: (_ for _ in ()).throw(DockerError("prerequisite-missing"))
    doc = obj.run()
    assert doc["status"] == "blocked" and doc["realE2E"] == "not-performed"


@pytest.mark.parametrize("fault", ["environment", "machine", "marker", "virtualization", "ubuntu", "authorization"])
def test_guest_identity_failure_boundaries(monkeypatch, fault):
    machine, daemon = "a" * 32, "b" * 64
    scope = safety.GuestScope(SESSION, f"run:{SESSION}", machine, daemon)
    monkeypatch.setattr(safety.os, "geteuid", lambda: 0)
    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(safety, "private_json", lambda p: {"session": SESSION, "machine": machine})
    def read(path, *args):
        return {"/etc/hostname": f"pd-e2e-{SESSION}", "/etc/machine-id": machine,
                "/etc/os-release": 'ID=ubuntu\nVERSION_ID="24.04"'}[str(path)]
    monkeypatch.setattr(safety.Path, "read_text", read)
    monkeypatch.setattr(safety, "run_bounded", lambda *a, **kw: b"kvm")
    monkeypatch.setattr(safety.LocalScope, "validate", lambda self: "/run/docker.sock")
    scope.validate()
    if fault == "environment":
        monkeypatch.setenv("DOCKER_CONTEXT", "production")
    elif fault == "machine":
        scope = replace(scope, machine="c" * 32)
    elif fault == "marker":
        monkeypatch.setattr(safety, "private_json", lambda p: {"session": "foreign", "machine": machine})
    elif fault == "virtualization":
        monkeypatch.setattr(safety, "run_bounded", lambda *a, **kw: b"lxc")
    elif fault == "ubuntu":
        monkeypatch.setattr(safety.Path, "read_text", lambda p, *a: read(p).replace("24.04", "22.04"))
    else:
        scope = replace(scope, authorization=f"prepare:{SESSION}")
    with pytest.raises(DockerError):
        scope.validate()


def test_pinned_daemon_mismatch_blocks_mutation(monkeypatch):
    adapter = object.__new__(safety.GuestDocker)
    adapter.deadline, adapter.test_session = None, SESSION
    adapter.guest = SimpleNamespace(validate=lambda **kw: None, daemon="pinned")
    calls = []
    def run(self, args, **kwargs):
        calls.append(args)
        return json.dumps({"ID": "foreign", "Name": f"pd-e2e-{SESSION}"}).encode()
    monkeypatch.setattr(safety.LocalDockerAdapter, "_run", run)
    with pytest.raises(DockerError, match="wrong-vm-daemon"):
        adapter._run(["network", "create", "never-created"])
    assert calls == [["info", "--format", "{{json .}}"]]


def test_cleanup_removes_network_by_immutable_id(tmp_path, monkeypatch):
    from scripts.reconciliation import DesiredState
    network = replace(resource("network", "reusable-name", {}), identifier="immutable-network-id")
    monkeypatch.setattr(cleanup, "build_desired_state", lambda *a: DesiredState(SHA, ()))
    monkeypatch.setattr(cleanup, "host_lock", nullcontext)
    monkeypatch.setattr(cleanup, "discover", lambda *a: None)
    monkeypatch.setattr(cleanup, "cleanup_plan", lambda *a: (network,))
    calls = []
    adapter = SimpleNamespace(_run=lambda args, **kw: calls.append(args))
    scope = SimpleNamespace(session=SESSION, directory=tmp_path)
    cleanup.clean(scope, adapter, set(), SHA)
    assert calls == [["network", "rm", "immutable-network-id"]]
