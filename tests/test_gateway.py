"""Synthetic gateway previews: routing intent never constitutes activation evidence."""

import builtins
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

from scripts import gateway as gateway
from scripts import gateway_cli as cli
from scripts import gateway_validation as validation
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan
from scripts.validate_registry import RegistryValidationError
from test_planning import SHA, FIXTURES, root, put, empty, observation, component


def candidate(desired, doc):
    observed = load_observed_state(doc)
    plan = build_reconciliation_plan(desired, observed)
    return gateway.build_gateway_candidate(desired, observed, plan)


def setup(root, *names):
    for name in names or ("support-assistant",):
        put(root, name)
    desired = build_desired_state(root, SHA)
    return desired, observation(desired)


@pytest.mark.parametrize("hostname", ["portfolio.example.xn--p1ai", "portfolio.example.co2",
    "portfolio.example.a", "portfolio.example." + "a" * 63,
    ".".join(["a" * 63] * 3 + ["a" * 61])])
def test_admitted_hostname_projects_to_desired_route(root, hostname):
    put(root, mutate=lambda s: s["ingress"].update(hostname=hostname))
    policy_path = root / "policy/admission.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    next(a for a in policy["applications"] if a["id"] == "support-assistant")["hostnames"] = [hostname]
    policy_path.write_text(yaml.safe_dump(policy))

    desired = build_desired_state(root, SHA)
    routes = gateway.desired_routes(desired)
    assert len(routes) == 1
    assert routes[0].hostname == hostname
    assert gateway.render_caddyfile(routes) == f"{hostname} {{\n    reverse_proxy support-assistant-frontend:8080\n}}\n"


def test_route_model_renderer_and_immutability(root):
    d, doc = setup(root, "project-router", "support-assistant", "internal-jobs")
    routes = gateway.desired_routes(d)
    assert len(routes) == 2
    assert {(r.alias, r.port) for r in routes} == {("project-router-web", 8080), ("support-assistant-frontend", 8080)}
    content = gateway.render_caddyfile(routes)
    assert content == gateway.render_caddyfile(reversed(routes))
    assert content.endswith("}\n") and content.count("reverse_proxy") == 2
    assert content.index("router.example.test") < content.index("support.example.test")
    assert "worker" not in content
    with pytest.raises(FrozenInstanceError):
        routes[0].port = 9999
    result = candidate(d, doc)
    assert result.document["status"] == "no-op"
    result.document["candidate"]["routes"].clear()
    assert len(result.document["candidate"]["routes"]) == 2
    assert result.to_json() == candidate(d, doc).to_json()
    reordered = replace(d, applications=tuple(reversed(d.applications)))
    doc["applications"].reverse()
    assert result == candidate(reordered, doc)


@pytest.mark.parametrize("name", ["backend", "frontend"])
def test_digest_and_git_revision_no_reload(root, name):
    d, doc = setup(root)
    put(root, mutate=lambda s: next(c for c in s["components"] if c["name"] == name).update(image=f"ghcr.io/fixtures/{name}@sha256:" + "b"*64))
    after = build_desired_state(root, "b"*40)
    result = candidate(after, doc).document
    assert result["reconciliationStatus"] == "changes"
    assert result["status"] == "no-op" and not result["gatewayChangeRequired"]
    assert result["current"]["caddyfile"] == result["candidate"]["caddyfile"] == result["desired"]["caddyfile"]


def test_reference_identifiers_do_not_change_gateway(root):
    d, doc = setup(root)
    doc["applications"][0]["configRefs"] = []
    component(doc, "frontend")["runtime"]["configRefs"] = []
    assert candidate(d, doc).document["status"] == "no-op"


def test_new_application_pending_preserves_unrelated_routes(root):
    d, doc = setup(root, "project-router")
    previous = candidate(d, doc).document["candidate"]
    put(root)
    after = build_desired_state(root, SHA)
    result = candidate(after, doc).document
    assert result["status"] == "pending" and result["gatewayChangeRequired"]
    assert result["candidate"] == previous == result["current"]
    assert len(result["desired"]["routes"]) == 2
    assert len(result["candidate"]["routes"]) == 1
    assert "target-readiness" in result["requiredChecks"]
    assert "proxy-network-membership" in result["requiredChecks"]
    assert not result["authorization"] and not result["applied"]


@pytest.mark.parametrize("network", [False, True])
def test_route_changes_are_pending_and_network_aware(root, network):
    d, doc = setup(root)
    app = doc["applications"][0]
    app["route"]["configuration"]["hostname"] = "old.example.test"
    if network:
        app["route"]["configuration"].update(component="backend")
        component(doc, "backend")["runtime"]["proxyNetwork"] = True
        component(doc, "frontend")["runtime"]["proxyNetwork"] = False
    result = candidate(d, doc).document
    outcome = result["outcomes"][0]
    assert outcome["kind"] == ("route-change" if network else "route-only")
    assert outcome["status"] == ("pending" if network else "changes")
    assert ("proxy-network-membership" in outcome["requiredChecks"]) == network
    assert result["candidate"] == result["current"] != result["desired"]
    assert "old.example.test" in result["candidate"]["caddyfile"]


@pytest.mark.parametrize("lifecycle", ["disabled", "removed", "ingress-removed"])
def test_lifecycle_never_deletes_active_routes(root, lifecycle):
    d, doc = setup(root)
    if lifecycle == "disabled":
        put(root, mutate=lambda s: s.update(enabled=False))
    elif lifecycle == "removed":
        (root / "registry/applications/support-assistant.yaml").unlink()
    else:
        put(root, mutate=lambda s: s.pop("ingress"))
    result = candidate(build_desired_state(root, SHA), doc).document
    assert result["candidate"] == result["current"]
    assert len(result["candidate"]["routes"]) == 1 and result["desired"]["routes"] == []
    assert result["status"] in ("pending", "blocked")
    assert "lifecycle-approval" in result["requiredChecks"]


def test_disabled_absent_internal_and_empty(root):
    put(root, mutate=lambda s: s.update(enabled=False))
    d = build_desired_state(root, SHA)
    result = candidate(d, empty()).document
    assert result["status"] == "no-op" and result["desired"]["outcome"] == "no-routes"
    assert result["candidate"]["caddyfile"] is None
    (root / "registry/applications/support-assistant.yaml").unlink()
    put(root, "internal-jobs")
    d = build_desired_state(root, SHA)
    assert candidate(d, observation(d)).document["candidate"]["outcome"] == "no-routes"
    (root / "registry/applications/internal-jobs.yaml").unlink()
    assert candidate(build_desired_state(root, SHA), empty()).document["status"] == "no-op"


@pytest.mark.parametrize("case", ["unknown", "incomplete", "untrusted", "foreign", "ambiguous", "duplicate"])
def test_unknown_and_conflicting_gateway_evidence_no_partial_config(root, case):
    d, doc = setup(root, "project-router", "support-assistant")
    route = doc["applications"][0]["route"]
    if case == "unknown": route.update(existence="unknown", configuration=None)
    elif case == "incomplete": doc["scope"]["complete"] = False
    elif case == "untrusted": doc["scope"]["trusted"] = False
    elif case in ("foreign", "ambiguous"): route["owner"]["manager"] = case
    else: doc["applications"][1]["route"]["configuration"]["hostname"] = route["configuration"]["hostname"]
    result = candidate(d, doc).document
    assert result["status"] == "blocked" and result["candidate"] is None
    assert result["current"] is None
    assert all(o["status"] == "blocked" for o in result["outcomes"])


def test_stage03_blockers_propagate_and_retain_known_routes(root):
    d, doc = setup(root)
    component(doc, "backend")["runtimeComplete"] = False
    result = candidate(d, doc).document
    assert result["status"] == "blocked" and result["outcomes"][0]["status"] == "blocked"
    assert result["candidate"] == result["current"]
    component(doc, "backend")["runtimeComplete"] = True
    component(doc, "frontend")["health"] = "degraded"
    assert candidate(d, doc).document["status"] == "blocked"


def test_matching_plan_is_required(root):
    d, doc = setup(root)
    obs = load_observed_state(doc)
    wrong = build_reconciliation_plan(d, load_observed_state(empty()))
    with pytest.raises(PlanningInputError, match="matching"):
        gateway.build_gateway_candidate(d, obs, wrong)


@pytest.mark.parametrize("field,value", [("hostname", "*.example.test"), ("hostname", ":443"),
    ("hostname", "*"), ("hostname", "localhost"), ("hostname", "127.0.0.1"),
    ("hostname", "portfolio.example.2co"), ("hostname", "portfolio.example.-co"),
    ("hostname", "portfolio.example.co-"), ("hostname", "portfolio.example.Co2"),
    ("hostname", "portfolio.example." + "a" * 64),
    ("hostname", "a" * 64 + ".example.test"),
    ("hostname", ".".join(["a" * 63] * 3 + ["a" * 62])),
    ("hostname", "example..test"), ("hostname", "example.test."),
    ("hostname", "example.test\n"),
    ("hostname", "good.example.test {\n tls SENSITIVE\n}"), ("hostname", "example.test/path"),
    ("hostname", "{$SENSITIVE}"), ("alias", "SENSITIVE"), ("port", 8080.0),
    ("manager", "foreign"), ("component", "web\nSENSITIVE")])
def test_route_injection_and_alias_rejection(field, value):
    params = dict(application="example", component="web", hostname="example.test", port=8080, alias="example-web")
    params[field] = value
    with pytest.raises(PlanningInputError) as error:
        gateway.GatewayRoute(**params)
    assert "SENSITIVE" not in str(error.value)


def test_duplicate_domains_aliases_and_projection_mismatch(root):
    d, _ = setup(root)
    route = gateway.desired_routes(d)[0]
    with pytest.raises(PlanningInputError, match="duplicate"):
        gateway.render_caddyfile([route, route])
    for mutation in (lambda s: s["ingress"].update(port=1234),
                     lambda s: s["ingress"].update(component="missing"),
                     lambda s: s["ingress"].update(component="worker")):
        # First boundary is the reused admission/semantic validator.
        put(root, mutate=mutation)
        with pytest.raises(RegistryValidationError):
            build_desired_state(root, SHA)
    app = d.applications[0]
    spec = app.spec
    spec["ingress"]["port"] = 1234
    from scripts.reconciliation import encode
    forged = replace(d, applications=(replace(app, spec_json=encode(spec)),))
    with pytest.raises(PlanningInputError, match="inconsistency"):
        gateway.desired_routes(forged)


def test_pure_planner_and_renderer_no_io(root, monkeypatch):
    d, doc = setup(root)
    obs = load_observed_state(doc)
    plan = build_reconciliation_plan(d, obs)
    expected = gateway.build_gateway_candidate(d, obs, plan)
    def forbidden(*args, **kwargs):
        pytest.fail("gateway pure function attempted I/O")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(__import__("os"), "system", forbidden)
    assert gateway.build_gateway_candidate(d, obs, plan) == expected
    assert gateway.render_caddyfile(gateway.desired_routes(d))


def test_official_binary_missing_and_no_routes(root, monkeypatch):
    monkeypatch.setattr(validation.shutil, "which", lambda _: None)
    d, _ = setup(root)
    result = validation.validate_with_caddy(gateway.desired_routes(d))
    assert result["status"] == "not-performed" and result["reason"] == "caddy-not-installed"
    monkeypatch.setattr(validation.shutil, "which", lambda _: "/trusted/caddy")
    assert validation.validate_with_caddy(())["reason"] == "explicit-no-routes-outcome"


@pytest.mark.parametrize("failure", [None, "adapt", "validate", "timeout", "version"])
def test_official_adapter_fixed_commands_and_bounded_failures(root, monkeypatch, failure):
    d, _ = setup(root)
    calls = []
    monkeypatch.setattr(validation.shutil, "which", lambda _: "/trusted/caddy")
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["shell"] is False and kwargs["timeout"] <= 15
        assert "XDG_DATA_HOME" in kwargs["env"]
        if args[1] == "version": kwargs["stdout"].write(b"v2.10.2")
        else:
            assert args[2:] == ["--config", str(Path(kwargs["cwd"]) / "candidate.Caddyfile"), "--adapter", "caddyfile"]
        if failure == "timeout": raise subprocess.TimeoutExpired(args, 15)
        return subprocess.CompletedProcess(args, int(args[1] == failure))
    monkeypatch.setattr(validation.subprocess, "run", run)
    result = validation.validate_with_caddy(gateway.desired_routes(d))
    assert result["status"] == ("passed" if failure is None else "failed")
    if failure == "validate": assert result["adapt"] and not result["validate"]
    assert all(c[1] in ("version", "adapt", "validate") for c in calls)
    if failure is None: assert result["version"] == "v2.10.2"


def test_cli_make_and_exit_codes(root, capsys, monkeypatch):
    d, doc = setup(root)
    path = root / "observed.json"
    path.write_text(json.dumps(doc))
    args = ["--root", str(root), "--observed", str(path), "--git-revision", SHA]
    assert cli.main(args) == 0
    first = capsys.readouterr()
    assert not first.err and json.loads(first.out)["status"] == "no-op"
    assert cli.main(args) == 0 and capsys.readouterr().out == first.out
    assert cli.main([*args, "--format", "caddyfile"]) == 0
    assert capsys.readouterr().out == gateway.render_caddyfile(gateway.desired_routes(d))
    monkeypatch.setattr(validation.shutil, "which", lambda _: None)
    assert cli.main([*args, "--official"]) == 4
    assert json.loads(capsys.readouterr().out)["officialValidation"]["status"] == "not-performed"
    doc["scope"]["trusted"] = False
    path.write_text(json.dumps(doc))
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["candidate"] is None
    assert cli.main([*args, "--format", "caddyfile"]) == 2
    assert not capsys.readouterr().out
    assert cli.main([*args[:-1], "SENSITIVE"]) == 1
    output = capsys.readouterr()
    assert not output.out and "SENSITIVE" not in output.err
    with pytest.raises(SystemExit) as error: cli.main([])
    assert error.value.code == 1
    capsys.readouterr()
    result = subprocess.run(["make", "gateway-render", f"PYTHON={sys.executable}",
        f"OBSERVED={FIXTURES / 'observed/noop.json'}", f"GIT_REVISION={SHA}", "GATEWAY_FORMAT=json"], capture_output=True, text=True)
    # The default registry now requests ProjectRouter; absent readiness retains pending intent.
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "pending"
    empty_args = ["--observed", str(FIXTURES / 'observed/noop.json'), "--git-revision", SHA, "--format", "caddyfile"]
    assert cli.main(empty_args) == 3 and not capsys.readouterr().out


def test_active_orphan_hostname_cannot_be_taken_over(root):
    d, doc = setup(root, "project-router", "support-assistant")
    (root / "registry/applications/project-router.yaml").unlink()
    # The orphan still owns this hostname in the supplied active state. Admission
    # permits the desired support hostname; observed routes need not match policy.
    doc["applications"][0]["route"]["configuration"]["hostname"] = "support.example.test"
    doc["applications"][1]["route"]["configuration"]["hostname"] = "old.example.test"
    result = candidate(build_desired_state(root, SHA), doc).document
    assert result["status"] == "blocked"
    assert result["candidate"] == result["current"]
    assert "desired-hostname-owned-by-another-active-route" in result["reasons"]
    assert all(o["status"] == "blocked" for o in result["outcomes"])


def test_invalid_candidate_and_official_failure_preserve_inputs(root, monkeypatch):
    d, doc = setup(root)
    obs = load_observed_state(doc)
    plan = build_reconciliation_plan(d, obs)
    accepted = gateway.build_gateway_candidate(d, obs, plan)
    monkeypatch.setattr(validation.shutil, "which", lambda _: "/trusted/caddy")
    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 1)
    monkeypatch.setattr(validation.subprocess, "run", fail)
    assert validation.validate_with_caddy(gateway.desired_routes(d))["status"] == "failed"
    assert gateway.build_gateway_candidate(d, obs, plan) == accepted
    bad_doc = obs.document
    bad_doc["applications"][0]["route"]["configuration"]["hostname"] = "bad.test { SENSITIVE }"
    with pytest.raises(PlanningInputError):
        load_observed_state(bad_doc)
    assert gateway.build_gateway_candidate(d, obs, plan) == accepted


@pytest.mark.parametrize("case", ["port", "worker", "missing", "network"])
def test_observed_ingress_requires_consistent_http_runtime(root, case):
    d, doc = setup(root)
    target = component(doc, "frontend")
    if case == "port": target["runtime"]["port"] = 9000
    elif case == "worker":
        target["runtime"]["type"] = "worker"
        target["runtime"]["health"] = {"strategy": "imageHealthcheck"}
        target["runtime"].pop("port")
    elif case == "missing": doc["applications"][0]["components"].remove(target)
    else: target["runtime"]["proxyNetwork"] = False
    result = candidate(d, doc).document
    assert result["status"] == "blocked" and result["candidate"] is None
    assert "observed-ingress-runtime-inconsistent-or-unknown" in result["reasons"]
