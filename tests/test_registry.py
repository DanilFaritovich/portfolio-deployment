"""Offline contract, admission and hostile-input regression tests."""

import copy
import hashlib
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from scripts import validate_registry as registry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "registry/applications").mkdir(parents=True)
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy/admission.yaml").write_bytes((FIXTURES / "admission.yaml").read_bytes())
    return tmp_path


def fixture(name="project-router"):
    return yaml.safe_load((FIXTURES / f"valid/{name}.yaml").read_text())


def put(root, document, filename=None):
    path = root / "registry/applications" / (filename or document["metadata"]["id"] + ".yaml")
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


def policy(root):
    return yaml.safe_load((root / "policy/admission.yaml").read_text())


def put_policy(root, document):
    (root / "policy/admission.yaml").write_text(yaml.safe_dump(document))


def set_field(document, path, value):
    for key in path[:-1]:
        document = document[key]
    document[path[-1]] = value


def test_whole_registry_offline_and_read_only(sandbox, monkeypatch):
    for name in ("project-router", "support-assistant", "internal-jobs"):
        put(sandbox, fixture(name))
    def snapshot():
        return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sandbox.rglob("*") if p.is_file()}
    before = snapshot()
    def network_forbidden(*args, **kwargs):
        pytest.fail("network access attempted")
    monkeypatch.setattr(socket, "socket", network_forbidden)
    first = registry.validate_registry(sandbox)
    assert [app["metadata"]["id"] for app in first.applications] == ["internal-jobs", "project-router", "support-assistant"]
    assert registry.validate_registry(sandbox) == first
    assert snapshot() == before


@pytest.mark.parametrize("name", ["project-router", "support-assistant", "internal-jobs"])
def test_valid_contracts(sandbox, name):
    put(sandbox, fixture(name))
    assert len(registry.validate_registry(sandbox).applications) == 1


@pytest.mark.parametrize("path,value", [
    (("apiVersion",), "portfolio.deployment/v2"),
    (("kind",), "Service"),
    (("metadata", "id"), "Bad_id"),
    (("metadata", "id"), "project-router\n"),
    (("metadata", "sourceRepository"), "https://github.com/fixtures/app"),
    (("spec", "enabled"), "false"),
    (("spec", "enabled"), 1),
    (("spec", "components"), []),
    (("spec", "components", 0, "name"), "web;id"),
    (("spec", "components", 0, "image"), "ghcr.io/fixtures/web:latest"),
    (("spec", "components", 0, "image"), "ghcr.io/fixtures/web:v1@sha256:" + "a"*64),
    (("spec", "components", 0, "image"), "docker.io/fixtures/web@sha256:" + "a"*64),
    (("spec", "components", 0, "image"), "ghcr.io/fixtures/web@sha256:" + "A"*64),
    (("spec", "components", 0, "image"), "ghcr.io/fixtures/web@sha256:" + "a"*63),
    (("spec", "components", 0, "image"), "ghcr.io/fixtures/web@sha256:" + "a"*64 + "\n"),
    (("spec", "components", 0, "image"), "ghcr.io/untrusted/web@sha256:" + "a"*64),
    (("spec", "components", 0, "platform"), "linux/arm64"),
    (("spec", "components", 0, "port"), 0),
    (("spec", "components", 0, "port"), 65536),
    (("spec", "components", 0, "port"), True),
    (("spec", "components", 0, "port"), 8080.0),
    (("spec", "components", 0, "port"), "8080"),
    (("spec", "components", 0, "health", "path"), "http://host/health"),
    (("spec", "components", 0, "health", "path"), "/../secret"),
    (("spec", "components", 0, "health", "path"), "/health?token=x"),
    (("spec", "components", 0, "health", "path"), "/health\r\n"),
    (("spec", "components", 0, "health"), {"strategy": "imageHealthcheck"}),
    (("spec", "ingress", "component"), "absent"),
    (("spec", "ingress", "port"), 8000),
    (("spec", "ingress", "hostname"), "unapproved.example.test"),
    (("spec", "ingress", "hostname"), "*.example.test"),
    (("spec", "ingress", "hostname"), "localhost"),
    (("spec", "ingress", "hostname"), "127.0.0.1"),
    (("spec", "ingress", "hostname"), "EXAMPLE.test"),
    (("spec", "ingress", "hostname"), "bad..test"),
    (("spec", "ingress", "hostname"), "example.test\n"),
    (("spec", "ingress"), [{"hostname": "router.example.test", "component": "web", "port":8080}]),
    (("spec", "components", 0, "dependsOn"), ["absent"]),
    (("spec", "components", 0, "dependsOn"), ["web"]),
    (("spec", "components", 0, "configRefs"), ["undeclared"]),
    (("spec", "secretRefs"), ["../secret"]),
    (("spec", "secretRefs"), [{"name":"token", "value":"secret"}]),
    (("spec", "secretRefs"), ["token", "token"]),
    (("spec", "storage"), {"type":"persistent", "volumes":[]}),
    (("spec", "storage"), {"type":"persistent", "volumes":[{"name":"data"}]}),
    (("spec", "components", 0, "volumeMounts"), [{"volume":"data", "mountPath":"/data"}]),
    (("spec", "components", 0, "volumeMounts"), [{"source":"/host", "mountPath":"/data"}]),
])
def test_invalid_contract(sandbox, path, value):
    document = fixture()
    set_field(document, path, value)
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("field", ["privileged", "network_mode", "ports", "command", "entrypoint", "cap_add", "devices", "volumes", "compose", "dockerArgs", "environment", "build"])
def test_runtime_escape_fields_forbidden(sandbox, field):
    document = fixture()
    document["spec"]["components"][0][field] = True
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError, match="schema"):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("path", [("apiVersion",), ("metadata", "sourceRepository"), ("spec", "enabled"), ("spec", "components",0,"health"), ("spec", "storage")])
def test_missing_required_fields(sandbox, path):
    document = fixture()
    target = document
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("path", sorted((FIXTURES / "invalid").glob("*.yaml")), ids=lambda p:p.stem)
def test_hostile_yaml_fixtures(sandbox, path):
    (sandbox / "registry/applications/project-router.yaml").write_bytes(path.read_bytes())
    with pytest.raises(registry.RegistryValidationError):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("data,message", [
    (b"a: " + b"x" * registry.MAX_FILE_BYTES, "byte limit"),
    (b"a: " + b"["*33 + b"0" + b"]"*33, "nesting limit"),
    (b"a: [" + b"0," * 4096 + b"]", "node limit"),
    (b"a: \xff", "UTF-8"),
    (b"a: 1\n1: value\n", "keys must be strings"),
])
def test_parser_limits(data, message):
    with pytest.raises(registry.RegistryValidationError, match=message):
        registry.load_document(data)


def test_cross_application_collisions(sandbox):
    a = fixture()
    put(sandbox, a)
    put(sandbox, a, "other.yaml")
    with pytest.raises(registry.RegistryValidationError) as error:
        registry.validate_registry(sandbox)
    assert "duplicate application ID" in str(error.value)
    assert "duplicate hostname" in str(error.value)
    assert "filename must match" in str(error.value)


def test_ambiguous_proxy_alias(sandbox):
    a, b = fixture(), fixture()
    a["metadata"]["id"] = "foo-bar"
    b["metadata"]["id"] = "foo"
    a["spec"]["components"][0]["name"] = "baz"
    b["spec"]["components"][0]["name"] = "bar-baz"
    a["spec"].pop("ingress")
    b["spec"].pop("ingress")
    put(sandbox, a)
    put(sandbox, b)
    with pytest.raises(registry.RegistryValidationError, match="alias collision"):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("mutation,message", [
    (lambda p:p["applications"].append(copy.deepcopy(p["applications"][0])), "duplicate application ID"),
    (lambda p:p["applications"][1].update(hostnames=["router.example.test"]), "duplicate hostname"),
    (lambda p:p["applications"].clear(), "not admitted"),
    (lambda p:p["applications"][0].update(sourceRepository="other/repository"), "source repository"),
    (lambda p:p["applications"][0].update(imageRepositories={"other":["ghcr.io/fixtures/web"]}), "image repository"),
    (lambda p:p.update(untrusted=True), "schema"),
    (lambda p:p.update(apiVersion="v2"), "unsupported"),
    (lambda p:p["applications"][0].update(platforms=["linux/arm64"]), "schema"),
])
def test_policy_is_independent_and_strict(sandbox, mutation, message):
    put(sandbox, fixture())
    document = policy(sandbox)
    mutation(document)
    put_policy(sandbox, document)
    with pytest.raises(registry.RegistryValidationError, match=message):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("case,message", [
    ("duplicate-component", "duplicate component"),
    ("cycle", "cyclic"),
    ("duplicate-volume", "duplicate volume"),
    ("duplicate-mount", "duplicate volume mount"),
    ("unused-volume", "declared and used"),
    ("socket", "reserved mount"),
    ("host-path", "schema"),
    ("worker-ingress", "HTTP component"),
    ("worker-http-health", "schema"),
])
def test_multi_component_boundaries(sandbox, case, message):
    document = fixture("support-assistant")
    spec = document["spec"]
    if case == "duplicate-component": spec["components"].append(copy.deepcopy(spec["components"][0]))
    elif case == "cycle": spec["components"][1]["dependsOn"] = ["frontend"]
    elif case == "duplicate-volume": spec["storage"]["volumes"].append({"name":"data"})
    elif case == "duplicate-mount": spec["components"][1]["volumeMounts"] += copy.deepcopy(spec["components"][1]["volumeMounts"])
    elif case == "unused-volume": spec["storage"]["volumes"].append({"name":"unused"})
    elif case == "socket": spec["components"][1]["volumeMounts"][0]["mountPath"] = "/var/run/docker.sock"
    elif case == "host-path": spec["components"][1]["volumeMounts"][0]["volume"] = "/host/data"
    elif case == "worker-ingress": spec["ingress"]["component"] = "worker"
    elif case == "worker-http-health": spec["components"][2]["health"] = {"strategy":"http","path":"/health"}
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError, match=message):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("case,message", [
    ("count", "file count"), ("total", "total byte"), ("size", "byte limit"),
    ("symlink", "regular file"), ("directory", "regular file"),
    ("missing-policy", "required file"), ("missing-registry", "directory"),
    ("wrong-extension", "filename"), ("nested", "regular file"),
])
def test_filesystem_boundaries(sandbox, case, message):
    directory = sandbox / "registry/applications"
    if case == "count":
        for i in range(registry.MAX_FILES + 1): (directory / f"app-{i}.yaml").write_text("{}")
    elif case == "total":
        for i in range(33): (directory / f"app-{i}.yaml").write_bytes(b" " * registry.MAX_FILE_BYTES)
    elif case == "size": (directory / "huge.yaml").write_bytes(b" " * (registry.MAX_FILE_BYTES + 1))
    elif case == "symlink": (directory / "link.yaml").symlink_to(sandbox / "policy/admission.yaml")
    elif case in ("directory", "nested"): (directory / "nested.yaml").mkdir()
    elif case == "missing-policy": (sandbox / "policy/admission.yaml").unlink()
    elif case == "missing-registry": directory.rmdir()
    elif case == "wrong-extension": (directory / "app.yml").write_text("{}")
    with pytest.raises(registry.RegistryValidationError, match=message):
        registry.validate_registry(sandbox)


def test_empty_registry_and_cli(sandbox):
    put_policy(sandbox, {"apiVersion": registry.VERSION, "kind":"AdmissionPolicy", "applications":[]})
    command = [sys.executable, str(registry.ROOT / "scripts/validate_registry.py"), "--root", str(sandbox)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0 and "0 applications" in result.stdout
    path = put(sandbox, fixture())
    path.write_text(path.read_text() + "spec: {}\n")
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == second.returncode == 1
    assert first.stdout == second.stdout and "duplicate YAML key" in first.stdout
    assert not first.stderr


def test_errors_do_not_echo_values(sandbox):
    document = fixture()
    document["spec"]["secretRefs"] = ["SENSITIVE-VALUE"]
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError) as error:
        registry.validate_registry(sandbox)
    assert "SENSITIVE-VALUE" not in str(error.value)


def test_missing_schema_is_actionable(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "ROOT", tmp_path / "no-schemas")
    with pytest.raises(registry.RegistryValidationError, match="schema unavailable"):
        registry.validate_registry(sandbox)


def test_invalid_policy_never_returns_partial_registry(sandbox):
    put(sandbox, fixture())
    (sandbox / "policy/admission.yaml").write_text("applications: [\n")
    with pytest.raises(registry.RegistryValidationError, match="policy/admission.yaml"):
        registry.validate_registry(sandbox)


def test_ingress_hostname_conflict_between_distinct_apps(sandbox):
    first, second = fixture(), fixture("support-assistant")
    second["spec"]["ingress"]["hostname"] = first["spec"]["ingress"]["hostname"]
    put(sandbox, first)
    put(sandbox, second)
    with pytest.raises(registry.RegistryValidationError, match="duplicate hostname"):
        registry.validate_registry(sandbox)


def test_long_proxy_alias_rejected(sandbox):
    document = fixture("internal-jobs")
    document["metadata"]["id"] = "a" * 32
    document["spec"]["components"][0]["name"] = "b" * 32
    put(sandbox, document)
    with pytest.raises(registry.RegistryValidationError, match="alias exceeds"):
        registry.validate_registry(sandbox)


@pytest.mark.parametrize("name", ["registry", "policy"])
def test_directory_symlinks_rejected(sandbox, name):
    path = sandbox / name
    moved = sandbox / (name + "-actual")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=True)
    with pytest.raises(registry.RegistryValidationError, match="directory"):
        registry.validate_registry(sandbox)
