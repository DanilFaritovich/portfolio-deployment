"""Synthetic Stage 03 contracts, isolation and hostile-input boundaries."""

import builtins
import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from scripts import planning_inputs as inputs
from scripts import reconciliation as engine
from scripts import plan_registry as cli

FIXTURES = Path(__file__).parent / "fixtures"
SHA = "a" * 40


def empty():
    return json.loads((FIXTURES / "observed/noop.json").read_text())


@pytest.fixture
def root(tmp_path):
    (tmp_path / "registry/applications").mkdir(parents=True)
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy/admission.yaml").write_bytes((FIXTURES / "admission.yaml").read_bytes())
    return tmp_path


def put(root, name="support-assistant", mutate=None):
    app = yaml.safe_load((FIXTURES / f"valid/{name}.yaml").read_text())
    app["spec"]["enabled"] = True
    if mutate:
        mutate(app["spec"])
    path = root / f"registry/applications/{name}.yaml"
    path.write_text(yaml.safe_dump(app))
    return app


def owner(app, component=None):
    return {"manager": "portfolio-deployment", "application": app,
            **({"component": component} if component else {})}


def observation(desired):
    doc = empty()
    for app in desired.applications:
        spec = app.spec
        components = []
        for component in spec["components"]:
            c = copy.deepcopy(component)
            name, image = c.pop("name"), c.pop("image")
            components.append({"name": name, "existence": "present", "owner": owner(app.id, name),
                               "runtime": c, "runtimeComplete": True, "health": "healthy",
                               "imageIdentity": {"pullReference": image, "pullKind": "manifest",
                                                 "manifestDigest": image.split("@")[1],
                                                 "platform": "linux/amd64", "relationship": "verified"}})
        doc["applications"].append({"id": app.id, "owner": owner(app.id), "existence": "present",
                                    "components": components, "storage": spec["storage"],
                                    "configRefs": spec["configRefs"], "secretRefs": spec["secretRefs"],
                                    "route": {"existence": "present" if spec["ingress"] else "absent",
                                              "owner": owner(app.id), "configuration": spec["ingress"]}})
    return doc


def plan(desired, observed):
    return engine.build_reconciliation_plan(desired, inputs.load_observed_state(observed))


def actions(result, action):
    return [e for e in result.document["entries"] if e["action"] == action]


def component(doc, name):
    return next(c for c in doc["applications"][0]["components"] if c["name"] == name)


def test_empty_and_explicit_absence(root):
    d = inputs.build_desired_state(root, SHA)
    assert plan(d, empty()).document["status"] == "no-op"
    put(root, "project-router")
    d = inputs.build_desired_state(root, SHA)
    result = plan(d, empty())
    assert [e["component"] for e in actions(result, "create")] == [None, "web"]
    doc = observation(d)
    app = doc["applications"][0]
    app.update(existence="absent", components=[])
    app["route"].update(existence="absent", configuration=None)
    assert plan(d, doc).to_json() == result.to_json()


@pytest.mark.parametrize("name,count", [("support-assistant", 3), ("internal-jobs", 1)])
def test_create_multicomponent_and_worker(root, name, count):
    put(root, name)
    d = inputs.build_desired_state(root, SHA)
    result = plan(d, empty())
    creates = [e for e in actions(result, "create") if e["component"]]
    assert len(creates) == count
    assert all("readiness" in e["requiredChecks"] for e in creates)
    if count == 3:
        frontend = next(e for e in creates if e["component"] == "frontend")
        assert frontend["after"]["dependsOn"] == ["backend"]
    else:
        assert not actions(result, "route-change")
        assert creates[0]["after"]["type"] == "worker"


@pytest.mark.parametrize("name", ["backend", "frontend"])
def test_component_update_is_scoped(root, name):
    put(root)
    before = inputs.build_desired_state(root, SHA)
    observed = observation(before)
    put(root, mutate=lambda s: next(c for c in s["components"] if c["name"] == name).update(image=f"ghcr.io/fixtures/{name}@sha256:" + "b" * 64))
    after = inputs.build_desired_state(root, "b" * 40)
    result = plan(after, observed)
    assert [e["component"] for e in actions(result, "update-component")] == [name]
    assert not actions(result, "route-only") and not actions(result, "route-change")
    assert result.document["status"] == "changes"


def test_noop_revision_determinism_canonical_and_reference_limit(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    observed = observation(d)
    first = plan(d, observed)
    assert first.document["status"] == "no-op"
    assert "content-and-versions-unobserved" in first.to_json()
    assert first.to_json() == plan(d, observed).to_json()
    observed["applications"][0]["components"].reverse()
    assert first.to_json() == plan(d, observed).to_json()
    d2 = inputs.build_desired_state(root, "b" * 40)
    assert not actions(plan(d2, observed), "update-component")
    assert engine.canonical({"a": ["z", "b"]}) == engine.canonical({"a": ["b", "z"]})


def test_hostname_route_only_and_target_network_change(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    observed = observation(d)
    observed["applications"][0]["route"]["configuration"]["hostname"] = "old.example.test"
    result = plan(d, observed)
    assert len(actions(result, "route-only")) == 1
    assert not actions(result, "update-component")
    observed["applications"][0]["route"]["configuration"]["component"] = "backend"
    component(observed, "backend")["runtime"]["proxyNetwork"] = True
    component(observed, "frontend")["runtime"]["proxyNetwork"] = False
    result = plan(d, observed)
    assert len(actions(result, "route-change")) == 1
    assert len(actions(result, "update-component")) == 2
    assert not actions(result, "route-only")


def test_other_application_untouched(root):
    put(root)
    put(root, "internal-jobs")
    d = inputs.build_desired_state(root, SHA)
    observed = observation(d)
    observed["applications"][1]["components"] = []
    result = plan(d, observed)
    assert all(e["status"] == "no-op" for e in result.document["entries"] if e["application"] == "internal-jobs")
    assert all("drift-missing-component" in e["reasons"] for e in actions(result, "create"))


@pytest.mark.parametrize("field,value", [("complete", False), ("trusted", False)])
def test_incomplete_not_absent(root, field, value):
    put(root)
    observed = empty()
    observed["scope"][field] = value
    result = plan(inputs.build_desired_state(root, SHA), observed)
    assert result.document["status"] == "blocked" and not actions(result, "create")
    assert plan(inputs.build_desired_state(root, SHA), {**observed, "applications": []}).document["status"] == "blocked"


@pytest.mark.parametrize("case,reason", [
    ("unknown", "unknown-state"), ("owner", "ownership-conflict"),
    ("foreign-app", "ownership-conflict"), ("oci", "oci-identity"),
    ("index-unknown", "oci-identity"), ("runtime", "unsupported-runtime"),
    ("storage", "storage-change"), ("mount", "mount-change"),
    ("route-unknown", "unknown-route"), ("route-owner", "ownership-conflict"),
])
def test_blocking_evidence(root, case, reason):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    c = component(doc, "backend")
    if case == "unknown": c.update(existence="unknown", runtime=None, imageIdentity=None)
    elif case == "owner": c["owner"]["application"] = "other"
    elif case == "foreign-app": doc["applications"][0]["owner"]["manager"] = "foreign"
    elif case == "oci": c["imageIdentity"]["manifestDigest"] = "sha256:" + "f" * 64
    elif case == "index-unknown": c["imageIdentity"].update(pullKind="index", relationship="unknown")
    elif case == "runtime": c["runtimeComplete"] = False
    elif case == "storage": doc["applications"][0]["storage"] = {"type":"stateless"}
    elif case == "mount": c["runtime"]["volumeMounts"][0]["mountPath"] = "/old/data"
    elif case == "route-unknown": doc["applications"][0]["route"].update(existence="unknown", configuration=None)
    elif case == "route-owner": doc["applications"][0]["route"]["owner"]["manager"] = "foreign"
    result = plan(d, doc)
    assert result.document["status"] == "blocked" and reason in result.to_json()
    assert not any(e["status"] == "changes" for e in result.document["entries"])


def test_oci_index_mapping_is_explicit(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    component(doc, "backend")["imageIdentity"].update(pullKind="index", manifestDigest="sha256:" + "b"*64)
    assert plan(d, doc).document["status"] == "no-op"


def test_disabled_orphan_and_removed_component(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    put(root, mutate=lambda s: s.update(enabled=False))
    disabled = inputs.build_desired_state(root, SHA)
    assert actions(plan(disabled, doc), "disable-pending-approval")
    assert not actions(plan(disabled, empty()), "create")
    (root / "registry/applications/support-assistant.yaml").unlink()
    assert actions(plan(inputs.build_desired_state(root, SHA), doc), "removal-pending-approval")
    put(root, mutate=lambda s: s.update(components=[c for c in s["components"] if c["name"] != "worker"]))
    assert actions(plan(inputs.build_desired_state(root, SHA), doc), "removal-pending-approval")[0]["component"] == "worker"


def test_degraded_independent_of_diff_and_unknown_priority(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    c = component(doc, "backend")
    c["health"] = "degraded"
    result = plan(d, doc)
    assert result.document["status"] == "degraded" and not actions(result, "update-component")
    c["runtime"]["port"] = 9090
    result = plan(d, doc)
    assert actions(result, "update-component") and actions(result, "health-observation")
    c["runtimeComplete"] = False
    assert plan(d, doc).document["status"] == "blocked"


def test_partial_missing_drift_and_app_blockers(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    doc["applications"][0]["components"] = [c for c in doc["applications"][0]["components"] if c["name"] != "backend"]
    assert [e["component"] for e in actions(plan(d, doc), "create")] == ["backend"]
    component(doc, "worker").update(existence="unknown", runtime=None, imageIdentity=None)
    result = plan(d, doc)
    assert actions(result, "create")[0]["status"] == "blocked"


@pytest.mark.parametrize("case", ["version", "duplicate-app", "duplicate-component", "type", "count", "unknown-field", "secret-value"])
def test_snapshot_structure(root, case):
    put(root)
    doc = observation(inputs.build_desired_state(root, SHA))
    if case == "version": doc["apiVersion"] = "portfolio.observed/v2"
    elif case == "duplicate-app": doc["applications"] *= 2
    elif case == "duplicate-component": doc["applications"][0]["components"] *= 2
    elif case == "type": doc["scope"]["complete"] = 1
    elif case == "count": doc["applications"] *= 129
    elif case == "unknown-field": component(doc, "backend")["runtime"]["privileged"] = True
    elif case == "secret-value": component(doc, "backend")["runtime"]["secretValues"] = {"token":"SENSITIVE"}
    with pytest.raises(engine.PlanningInputError) as error:
        inputs.load_observed_state(doc)
    assert "SENSITIVE" not in str(error.value)


@pytest.mark.parametrize("case", ["missing", "large", "link", "parent-link", "parent", "directory", "fifo", "depth", "duplicate-key", "utf8", "float"])
def test_local_snapshot_boundaries(tmp_path, case):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(empty()))
    if case == "missing": path.unlink()
    elif case == "large": path.write_bytes(b" " * (inputs.MAX_BYTES + 1))
    elif case == "link":
        real = tmp_path / "real.json"
        path.rename(real)
        path.symlink_to(real)
    elif case == "parent-link":
        link = tmp_path / "alias"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / "state.json"
    elif case == "parent": path = tmp_path / ".." / tmp_path.name / "state.json"
    elif case == "directory": path.unlink(); path.mkdir()
    elif case == "fifo":
        import os
        path.unlink()
        os.mkfifo(path)
    elif case == "depth": path.write_text("["*33 + "0" + "]"*33)
    elif case == "duplicate-key": path.write_text('{"secret":"SENSITIVE","secret":0}')
    elif case == "utf8": path.write_bytes(b"\xff")
    elif case == "float": path.write_text('{"a":NaN}')
    with pytest.raises(engine.PlanningInputError) as error:
        inputs.load_observed_state(path)
    assert "SENSITIVE" not in str(error.value)


@pytest.mark.parametrize("sha", ["", "abc", "A"*40, "a"*41, "a"*40+"\n", None])
def test_revision_rejected(root, sha):
    with pytest.raises(engine.PlanningInputError, match="full lowercase"):
        inputs.build_desired_state(root, sha)


def test_deep_immutability_and_engine_no_io(root, monkeypatch):
    put(root)
    desired = inputs.build_desired_state(root, SHA)
    document = observation(desired)
    original = copy.deepcopy(document)
    observed = inputs.load_observed_state(document)
    expected = engine.build_reconciliation_plan(desired, observed)
    assert document == original
    document["applications"].clear()
    desired.applications[0].spec["components"].clear()
    observed.document["applications"].clear()
    with pytest.raises(FrozenInstanceError):
        desired.git_revision = "b"*40
    def forbidden(*args, **kwargs):
        pytest.fail("pure planner attempted I/O")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(__import__("os"), "system", forbidden)
    assert engine.build_reconciliation_plan(desired, observed) == expected


def test_cli_and_make_product_surface(root, capsys):
    args = ["--root", str(root), "--observed", str(FIXTURES / "observed/noop.json"), "--git-revision", SHA, "--format", "json"]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["executed"] is False and not output.err
    assert cli.main([*args[:3], str(FIXTURES / "observed/incomplete.json"), *args[4:]]) == 2
    capsys.readouterr()
    assert cli.main([*args[:-3], "bad", "--format", "json"]) == 1
    assert not capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        cli.main(["--git-revision", SHA])
    assert error.value.code == 1
    result = subprocess.run(["make", "plan", f"PYTHON={sys.executable}", f"OBSERVED={FIXTURES / 'observed/noop.json'}", f"GIT_REVISION={SHA}", "PLAN_FORMAT=json"], capture_output=True, text=True)
    # Empty observations differ from the registered first-rollout desired state.
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "changes"
    assert json.loads(result.stdout)["executed"] is False
    assert subprocess.run(["make", "plan", f"PYTHON={sys.executable}", "OBSERVED=", "GIT_REVISION="], capture_output=True).returncode != 0


@pytest.mark.parametrize("field,value", [
    ("type", "worker"), ("port", 9090),
    ("health", {"strategy":"http", "path":"/ready"}),
    ("dependsOn", ["worker"]), ("configRefs", []), ("secretRefs", []),
])
def test_supported_runtime_fields_are_effective(root, field, value):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    doc = observation(d)
    c = component(doc, "backend")
    c["runtime"][field] = value
    if field == "type":
        c["runtime"].pop("port")
        c["runtime"]["health"] = {"strategy":"imageHealthcheck"}
    result = plan(d, doc)
    assert [e["component"] for e in actions(result, "update-component")] == ["backend"]


def test_unknown_health_and_runtime_absence(root):
    put(root)
    d = inputs.build_desired_state(root, SHA)
    for field, value in [("health", "unknown"), ("runtime", None), ("imageIdentity", None)]:
        doc = observation(d)
        component(doc, "backend")[field] = value
        assert plan(d, doc).document["status"] == "blocked"


def test_multiple_errors_are_sorted_and_not_values(root):
    put(root)
    doc = observation(inputs.build_desired_state(root, SHA))
    doc["applications"][0]["components"] *= 2
    doc["applications"] *= 2
    with pytest.raises(engine.PlanningInputError) as error:
        inputs.load_observed_state(doc)
    assert str(error.value) == "duplicate application identity; duplicate component identity"


def test_native_input_limits_and_nonfinite():
    for doc in [{"a": float("inf")}, {"a": "x"*513}, {"a": object()}]:
        with pytest.raises(engine.PlanningInputError):
            inputs.load_observed_state(doc)
    cycle = {}
    cycle["a"] = cycle
    with pytest.raises(engine.PlanningInputError, match="nesting/node"):
        inputs.load_observed_state(cycle)


def test_cli_degraded_and_json_snapshot_fixture(root, capsys):
    put(root)
    desired = inputs.build_desired_state(root, SHA)
    doc = json.loads((FIXTURES / "observed/support-assistant.json").read_text())
    assert plan(desired, doc).document["status"] == "no-op"
    component(doc, "backend")["health"] = "degraded"
    path = root / "observed.json"
    path.write_text(json.dumps(doc))
    assert cli.main(["--root", str(root), "--observed", str(path), "--git-revision", SHA]) == 3
    assert "degraded" in capsys.readouterr().out


@pytest.mark.parametrize("lifecycle", ["enabled", "disabled", "removed"])
@pytest.mark.parametrize("route_state", ["unknown", "absent"])
def test_absent_application_preserves_route_evidence(root, lifecycle, route_state):
    put(root, "project-router")
    desired = inputs.build_desired_state(root, SHA)
    document = observation(desired)
    app = document["applications"][0]
    app.update(existence="absent", components=[])
    app["route"].update(existence=route_state, configuration=None)
    if lifecycle == "disabled":
        put(root, "project-router", mutate=lambda spec: spec.update(enabled=False))
    elif lifecycle == "removed":
        (root / "registry/applications/project-router.yaml").unlink()
    desired = inputs.build_desired_state(root, SHA)
    observed = inputs.load_observed_state(document)
    result = engine.build_reconciliation_plan(desired, observed)
    assert observed.document["applications"][0]["route"]["existence"] == route_state
    if route_state == "unknown":
        assert result.document["status"] == "blocked"
        assert result.document["applications"] == [{"id": "project-router", "status": "blocked"}]
        assert all(entry["status"] == "blocked" for entry in result.document["entries"])
        assert actions(result, "blocked")[0]["reasons"] == ["unknown-route-or-ownership-conflict"]
    elif lifecycle == "enabled":
        assert result.document["status"] == "changes"
        assert [entry["component"] for entry in actions(result, "create")] == [None, "web"]
        assert result.to_json() == plan(desired, empty()).to_json()
    else:
        assert result.document["status"] == "no-op"
        assert all(entry["action"] == "no-op" for entry in result.document["entries"])
