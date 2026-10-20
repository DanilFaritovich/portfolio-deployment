"""Explicit cleanup of owned containers/empty networks. Volumes and journals stay."""
from scripts.compose import LABEL, render_compose
from scripts.deployment import host_lock
from scripts.docker_discovery import discover
from scripts.e2e.safety import require
from scripts.local_docker_smoke import verify_cleanup
from scripts.planning_inputs import build_desired_state


def cleanup_plan(observation, artifacts, session, baseline):
    # The proxy is separately provisioned, so handle it outside Compose identity.
    proxy_name = f"pd-smoke-{session}-proxy"
    proxies = [r for r in observation.resources if r.kind == "network" and r.name == proxy_name]
    require(len(proxies) <= 1, "cleanup-ambiguous-proxy")
    for proxy in proxies:
        require(proxy.facts["labels"] == {f"{LABEL}.managed": "true", f"{LABEL}.role": "proxy",
                                         f"{LABEL}.test-session": session} and
                proxy.facts["driver"] == "bridge" and proxy.facts["internal"] is True and
                (proxy.kind, proxy.identifier) not in baseline, "cleanup-foreign-proxy")
    from dataclasses import replace
    subset = replace(observation, resources=tuple(r for r in observation.resources if r not in proxies))
    targets = verify_cleanup(subset, artifacts, session, baseline)
    containers = {r.identifier for r in targets if r.kind == "container"}
    for r in (*targets, *proxies):
        if r.kind == "network":
            require(not set(r.facts["containers"]) - containers, "cleanup-foreign-network-attachment")
    # No persistent or anonymous volume deletion, including explicit clean.
    return tuple(r for r in targets if r.kind != "volume") + tuple(proxies)


def clean(scope, adapter, baseline, revision):
    desired = build_desired_state(scope.directory / "registry", revision)
    artifacts = [render_compose(a, test_session=scope.session) for a in desired.applications]
    with host_lock():
        targets = cleanup_plan(discover(adapter, ()), artifacts, scope.session, baseline)
        for kind in ("container", "network"):
            for target in targets:
                if target.kind != kind:
                    continue
                current = cleanup_plan(discover(adapter, ()), artifacts, scope.session, baseline)
                require(any((r.kind, r.identifier, r.name, r.facts["labels"]) ==
                            (target.kind, target.identifier, target.name, target.facts["labels"]) for r in current),
                        "cleanup-resource-changed")
                if kind == "container":
                    adapter._run(["container", "stop", "--time", "5", target.identifier], timeout=15)
                    checked = cleanup_plan(discover(adapter, ()), artifacts, scope.session, baseline)
                    require(any(r.identifier == target.identifier and r.kind == kind for r in checked),
                            "cleanup-container-changed")
                adapter._run([kind, "rm", target.identifier])
    return {"status": "passed", "removed": len(targets), "volumes": "preserved", "journal": "preserved", "vm": "preserved"}
