"""Offline registration and admission regressions; no live release authorization."""

import copy
from pathlib import Path
import socket
import subprocess

import pytest
import yaml

from scripts.compose import LABEL, render_compose
from scripts.gateway import desired_routes, render_caddyfile
from scripts.planning_inputs import build_desired_state
from scripts.validate_registry import RegistryValidationError, validate_registry

ROOT = Path(__file__).resolve().parents[1]
# Synthetic identity used only after substituting the inactive template in tmp_path.
DIGEST = "sha256:" + "a" * 64
REVISION = "a" * 40


@pytest.fixture
def registration(tmp_path):
    (tmp_path / "registry/applications").mkdir(parents=True)
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy/admission.yaml").write_bytes((ROOT / "policy/admission.yaml").read_bytes())
    template = (ROOT / "docs/templates/project-router.yaml").read_text()
    document = yaml.safe_load(template.replace("${PROJECT_ROUTER_DIGEST}", DIGEST))
    return tmp_path, document


def write(root, document):
    path = root / "registry/applications/project-router.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


def test_registered_project_router_contract_is_offline_and_has_no_admin_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("manifest projection attempted external I/O")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    registry = validate_registry(ROOT)
    assert [doc["metadata"]["id"] for doc in registry.applications] == ["project-router"]
    document = registry.applications[0]
    assert document["spec"]["enabled"] is True
    desired = build_desired_state(ROOT, REVISION)
    app = desired.applications[0]
    component = app.spec["components"][0]
    assert component["health"] == {"strategy": "http", "path": "/en/"}
    assert component["port"] == 8080
    assert app.spec["storage"]["type"] == "stateless"
    assert not app.spec["configRefs"] and not app.spec["secretRefs"]
    assert not component["configRefs"] and not component["secretRefs"]
    model = render_compose(app).model
    assert set(model["services"]) == {"web"}
    web = model["services"]["web"]
    assert not set(web) & {"ports", "volumes", "environment", "secrets", "configs", "build", "privileged"}
    assert web["image"] == document["spec"]["components"][0]["image"]
    assert web["user"] == "101:101" and web["read_only"] is True
    assert web["cap_drop"] == ["ALL"]
    assert web["security_opt"] == ["no-new-privileges:true"]
    assert web["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777"]
    assert "healthcheck" not in web  # Preserve the verified image healthcheck.
    assert model["networks"]["private"]["internal"] is True
    assert model["networks"]["proxy"] == {"name": "portfolio-proxy", "external": True}
    assert web["networks"]["proxy"]["aliases"] == ["project-router-web"]
    from scripts.production_integration.gateway import content
    text = content(desired_routes(desired))
    assert "admin unix//run/portfolio-caddy-admin/admin.sock|0600" in text
    assert "reverse_proxy project-router-web:8080" in text
    assert "/srv/portfolio/caddy-admin" not in str(model)
    assert "/run/portfolio-caddy-admin" not in str(model)


def test_template_cannot_enter_registry_without_digest(registration):
    root, _ = registration
    (root / "registry/applications/project-router.yaml").write_bytes(
        (ROOT / "docs/templates/project-router.yaml").read_bytes())
    with pytest.raises(RegistryValidationError):
        validate_registry(root)


def test_disabled_registration_validates_offline_without_routes(registration, monkeypatch):
    root, document = registration
    path = write(root, document)
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("offline contract contacted external process/network")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    desired = build_desired_state(root, REVISION)
    assert desired_routes(desired) == ()
    assert render_caddyfile(desired_routes(desired)) is None
    assert path.read_bytes() == before
    assert desired.applications[0].spec["components"][0]["health"] == {"strategy": "http", "path": "/en/"}


def test_manager_compose_and_gateway_share_canonical_contract(registration):
    root, document = registration
    # Enabled only in an isolated synthetic test registry, never in production.
    document["spec"]["enabled"] = True
    write(root, document)
    desired = build_desired_state(root, REVISION)
    artifact = render_compose(desired.applications[0])
    model = artifact.model
    assert artifact.project == "portfolio-project-router"
    assert model["networks"]["private"]["name"] == "portfolio-project-router-private"
    assert model["networks"]["proxy"] == {"name": "portfolio-proxy", "external": True}
    assert set(model["services"]) == {"web"}
    web = model["services"]["web"]
    assert web["networks"]["proxy"]["aliases"] == ["project-router-web"]
    assert web["image"] == "ghcr.io/danilfaritovich/project-router@" + DIGEST
    assert web["platform"] == "linux/amd64"
    assert web["labels"][LABEL + ".application"] == "project-router"
    assert web["labels"][LABEL + ".component"] == "web"
    assert not any(k in web for k in ("ports", "build", "container_name", "volumes", "environment"))
    assert "volumes" not in model
    assert render_caddyfile(desired_routes(desired)) == (
        "danil-miftakhov.dev {\n    reverse_proxy project-router-web:8080\n}\n")
    assert render_compose(desired.applications[0]) == artifact


@pytest.mark.parametrize("field,value", [
    ("source", "Untrusted/project-router"),
    ("image", "ghcr.io/untrusted/project-router@" + DIGEST),
    ("image", "ghcr.io/danilfaritovich/project-router-extra@" + DIGEST),
    ("image", "ghcr.io/danilfaritovich/project-router:main"),
    ("image", "ghcr.io/danilfaritovich/project-router:main@" + DIGEST),
    ("image", "ghcr.io/danilfaritovich/project-router@sha256:" + "A" * 64),
    ("image", "ghcr.io/danilfaritovich/project-router@" + "a" * 64),
    ("image", "sha256:" + "a" * 64),
    ("platform", "linux/arm64"),
    ("hostname", "www.danil-miftakhov.dev"),
    ("hostname", "router.example.test"),
    ("hostname", "*.danil-miftakhov.dev"),
    ("health", {"strategy": "http", "path": "/health"}),
    ("health", {"strategy": "imageHealthcheck"}),
    ("port", 80),
    ("name", "project-router"),
    ("storage", {"type": "persistent", "volumes": []}),
    ("ingress", None),
    ("extra-component", True),
    ("configRefs", ["site-config"]),
    ("secretRefs", ["site-secret"]),
])
def test_production_admission_rejects_contract_drift_even_disabled(registration, field, value):
    root, doc = registration
    spec = doc["spec"]
    component = spec["components"][0]
    if field == "source":
        doc["metadata"]["sourceRepository"] = value
    elif field == "hostname":
        spec["ingress"]["hostname"] = value
    elif field == "ingress":
        spec.pop("ingress")
    elif field in ("storage", "configRefs", "secretRefs"):
        spec[field] = value
    elif field == "extra-component":
        extra = copy.deepcopy(component)
        extra["name"] = "other"
        spec["components"].append(extra)
    else:
        component[field] = value
        if field in ("port", "name"):
            spec["ingress"]["port" if field == "port" else "component"] = value
    write(root, doc)
    with pytest.raises(RegistryValidationError):
        validate_registry(root)


@pytest.mark.parametrize("change", ["origin-host", "component", "unknown", "unsafe-origin", "missing-path"])
def test_policy_contract_itself_is_closed_and_consistent(registration, change):
    root, _ = registration
    path = root / "policy/admission.yaml"
    policy = yaml.safe_load(path.read_text())
    site = policy["applications"][0]["staticSite"]
    if change == "origin-host":
        site["buildOrigin"] = "https://other.dev"
    elif change == "component":
        site["component"] = "other"
    elif change == "unknown":
        site["skipValidation"] = True
    elif change == "unsafe-origin":
        site["buildOrigin"] = "https://danil-miftakhov.dev/path"
    else:
        site.pop("readinessPath")
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(RegistryValidationError):
        validate_registry(root)


def test_hostname_cannot_be_admitted_to_another_owner(registration):
    root, _ = registration
    path = root / "policy/admission.yaml"
    policy = yaml.safe_load(path.read_text())
    other = copy.deepcopy(policy["applications"][0])
    other["id"] = "other"
    policy["applications"].append(other)
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(RegistryValidationError, match="duplicate hostname"):
        validate_registry(root)


@pytest.mark.parametrize("key", ["configRefs", "secretRefs", "volumeMounts"])
def test_static_site_rejects_otherwise_valid_runtime_state(registration, key):
    root, doc = registration
    spec = doc["spec"]
    if key == "volumeMounts":
        spec["storage"] = {"type": "persistent", "volumes": [{"name": "data"}]}
        spec["components"][0][key] = [{"volume": "data", "mountPath": "/data"}]
    else:
        spec[key] = ["site-reference"]
        spec["components"][0][key] = ["site-reference"]
    write(root, doc)
    with pytest.raises(RegistryValidationError, match="static site"):
        validate_registry(root)


def test_make_compose_preview_with_explicit_inactive_registration(registration):
    root, doc = registration
    write(root, doc)
    import json
    import sys
    result = subprocess.run([
        "make", "compose-render", "PYTHON=" + sys.executable,
        "REGISTRY_ROOT=" + str(root), "APPLICATION=project-router", "GIT_REVISION=" + REVISION,
    ], cwd=ROOT, text=True, capture_output=True, timeout=15, check=True)
    model = json.loads(result.stdout)
    assert model["name"] == "portfolio-project-router"
    assert model["services"]["web"]["networks"]["proxy"]["aliases"] == ["project-router-web"]
