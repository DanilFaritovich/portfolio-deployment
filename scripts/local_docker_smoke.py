"""Explicit disposable local Docker smoke. Never called by offline checks."""

import argparse
from dataclasses import replace
import re
import sys

from scripts.compose import LABEL, render_compose
from scripts.deployment import host_lock, preflight_resources
from scripts.docker_adapter import DockerError, LocalDockerAdapter, LocalScope
from scripts.docker_discovery import discover, container_runtime, owned_resource
from scripts.reconciliation import DesiredApplication, encode, normalized_spec

IMAGE = re.compile(r"ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}")


def test_application(image, application="sample"):
    """Test-only source injection; does not register or admit any production image."""
    components = [{"name": name, "image": image, "platform": "linux/amd64", "type": "worker",
                   "health": {"strategy": "imageHealthcheck"},
                   "volumeMounts": [{"volume": "data", "mountPath": "/pd-smoke-data"}] if name == "backend" else []}
                  for name in ("backend", "frontend")]
    return DesiredApplication(application, "fixtures/disposable-smoke", encode(normalized_spec(
        {"enabled": True, "components": components, "storage": {"type": "persistent", "volumes": [{"name": "data"}]}})))


def session_resources(observation, session):
    return tuple(r for r in observation.resources if r.facts["labels"].get(f"{LABEL}.test-session") == session)


def verify_cleanup(observation, artifacts, session, baseline):
    """Validate the entire deletion set before deleting anything. No adoption."""
    if not observation.complete:
        raise DockerError("cleanup-discovery-incomplete")
    targets = session_resources(observation, session)
    known = {a.project: a for a in artifacts}
    for r in observation.resources:
        in_namespace = r.name.startswith(f"pd-smoke-{session}-")
        if in_namespace and r not in targets:
            raise DockerError("cleanup-ownership-conflict")
    for r in targets:
        if (r.kind, r.identifier) in baseline:
            raise DockerError("cleanup-resource-predates-session")
        project = r.facts["labels"].get("com.docker.compose.project")
        a = known.get(project)
        if not a:
            raise DockerError("cleanup-unknown-project")
        section = {"container": "services", "network": "networks", "volume": "volumes"}[r.kind]
        key_label = {"container": "service", "network": "network", "volume": "volume"}[r.kind]
        key = r.facts["labels"].get(f"com.docker.compose.{key_label}")
        item = a.model.get(section, {}).get(key)
        expected_labels = {k: v for k, v in (item or {}).get("labels", {}).items() if k != f"{LABEL}.runtime"}
        if not item or not owned_resource(r, expected_labels, project, key):
            raise DockerError("cleanup-ownership-conflict")
        expected_name = f"{project}-{key}-1" if r.kind == "container" else item["name"]
        if r.name != expected_name:
            raise DockerError("cleanup-name-conflict")
    return targets


def cleanup(adapter, artifacts, baseline):
    fresh = discover(adapter, ())
    targets = verify_cleanup(fresh, artifacts, adapter.test_session, baseline)
    # Recheck immediately before each deletion. Do not use production executor lifecycle.
    for kind in ("container", "network", "volume"):
        for r in targets:
            if r.kind != kind:
                continue
            now = discover(adapter, ())
            current = verify_cleanup(now, artifacts, adapter.test_session, baseline)
            if not any(x.kind == r.kind and x.identifier == r.identifier and x.name == r.name and
                       x.facts["labels"] == r.facts["labels"] for x in current):
                raise DockerError("cleanup-resource-changed")
            if kind == "container":
                adapter._run(["container", "stop", "--time", "5", r.identifier], timeout=15)
                # Re-discovery after stop is required, state changes are expected.
                checked = verify_cleanup(discover(adapter, ()), artifacts, adapter.test_session, baseline)
                if not any(x.kind == kind and x.identifier == r.identifier for x in checked):
                    raise DockerError("cleanup-resource-changed")
                adapter._run(["container", "rm", r.identifier])
            else:
                adapter._run([kind, "rm", r.name])


def smoke_apply(adapter, app, artifact, references, service, previous_artifact=None):
    """Test-only convergence from actual inspect evidence; never supplies Stage 03 scope."""
    adapter.authorize(artifact)
    observation = discover(adapter, references)
    blockers = preflight_resources(app, artifact, observation)
    if blockers:
        raise DockerError(blockers[0])
    wanted = artifact.model["services"][service]
    current = [r for r in observation.resources if r.kind == "container" and
               r.facts["labels"].get("com.docker.compose.project") == artifact.project and
               r.facts["labels"].get("com.docker.compose.service") == service]
    if len(current) > 1:
        raise DockerError("duplicate-component-containers")
    image = next(i for i in observation.images if i.reference == wanted["image"])
    if current and current[0].facts["state"].get("Status") == "running" and container_runtime(
            current[0], artifact, service, image) and current[0].facts["labels"].get(f"{LABEL}.runtime") == wanted["labels"][f"{LABEL}.runtime"]:
        return "no-op"
    if current:
        old = previous_artifact.model["services"][service] if previous_artifact else None
        old_image = next((i for i in observation.images if old and i.reference == old["image"]), None)
        if (not old_image or current[0].facts["state"].get("Status") != "running" or
            current[0].facts["labels"].get(f"{LABEL}.runtime") != old["labels"][f"{LABEL}.runtime"] or
            not container_runtime(current[0], previous_artifact, service, old_image)):
            raise DockerError("smoke-before-runtime-unverified")
    adapter.up(artifact, service)
    return "applied-unverified"


def smoke(adapter, image_a, image_b):
    session = adapter.test_session
    references = (image_a, image_b)
    applications = [test_application(image_a), test_application(image_a, "sentinel")]
    artifacts = [render_compose(a, test_session=session) for a in applications]
    adapter.prerequisites()
    initial = discover(adapter, references)
    if not initial.complete:
        return {"status": "not-performed", "reason": "docker-discovery-incomplete"}
    if any(r.name.startswith(f"pd-smoke-{session}-") or r in session_resources(initial, session) for r in initial.resources):
        return {"status": "not-performed", "reason": "session-namespace-already-exists"}
    if len(initial.images) != len(set(references)) or any(not i.verified for i in initial.images):
        return {"status": "not-performed", "reason": "preloaded-manifest-images-with-engine-descriptors-required"}
    if image_a == image_b:
        return {"status": "not-performed", "reason": "two-distinct-immutable-images-required"}
    baseline = {(r.kind, r.identifier) for r in initial.resources}
    checks = []
    result = {"status": "failed", "reason": "smoke-incomplete"}
    try:
        for app, artifact in zip(applications, artifacts):
            blockers = preflight_resources(app, artifact, discover(adapter, references))
            if blockers:
                raise DockerError(blockers[0])
            for name in artifact.model["services"]:
                smoke_apply(adapter, app, artifact, references, name)
        checks.append("create-two-projects-and-components")
        before = discover(adapter, references)
        if not before.complete:
            raise DockerError("smoke-observation-incomplete")
        first = artifacts[0]
        spec = applications[0].spec
        next(c for c in spec["components"] if c["name"] == "backend")["image"] = image_b
        updated_app = replace(applications[0], spec_json=encode(spec))
        updated = render_compose(updated_app, test_session=session)
        blockers = preflight_resources(updated_app, updated, before)
        if blockers:
            raise DockerError(blockers[0])
        smoke_apply(adapter, updated_app, updated, references, "backend", previous_artifact=first)
        after = discover(adapter, references)
        if not after.complete:
            raise DockerError("smoke-observation-incomplete")
        def inventory(obs, kind):
            return {r.name: r.identifier for r in obs.resources if r.kind == kind}
        before_ids, after_ids = inventory(before, "container"), inventory(after, "container")
        backend_name = f"{first.project}-backend-1"
        if before_ids.get(backend_name) == after_ids.get(backend_name):
            raise DockerError("smoke-update-not-observed")
        if {k: v for k, v in before_ids.items() if k != backend_name} != {k: v for k, v in after_ids.items() if k != backend_name}:
            raise DockerError("smoke-unrelated-container-changed")
        if inventory(before, "volume") != inventory(after, "volume"):
            raise DockerError("smoke-volume-identity-changed")
        checks.extend(("scoped-update", "unrelated-components-and-projects-preserved", "volume-identity-preserved"))
        images = {i.reference: i for i in after.images}
        for r in session_resources(after, session):
            if r.kind == "container":
                a = updated if r.name.startswith(first.project + "-") else artifacts[1]
                name = r.facts["labels"].get("com.docker.compose.service")
                if not container_runtime(r, a, name, images[a.model["services"][name]["image"]]) or r.facts["state"].get("Status") != "running":
                    raise DockerError("smoke-runtime-mismatch")
        checks.append("no-published-ports-and-supported-runtime")
        repeat_status = smoke_apply(adapter, updated_app, updated, references, "backend")
        repeat = discover(adapter, references)
        if repeat_status != "no-op" or not repeat.complete or inventory(repeat, "container") != after_ids:
            raise DockerError("smoke-noop-recreated-container")
        checks.append("repeat-noop-no-mutation")
        try:
            adapter.inspect("container", f"pd-smoke-{session}-intentionally-missing")
            raise DockerError("smoke-expected-failure-missing")
        except DockerError as error:
            if error.code != "docker-command-failed" or error.unknown:
                raise
        checks.append("basic-command-failure")
        result = {"status": "passed", "checks": checks, "releaseSuccess": False}
    except DockerError as error:
        result = {"status": "unknown" if error.unknown else "failed", "reason": error.code, "checks": checks}
    finally:
        try:
            cleanup(adapter, artifacts, baseline)
            result["cleanup"] = "completed"
        except DockerError as error:
            result["cleanup"] = "manual-review-required"
            result["cleanupReason"] = error.code
            result["status"] = "failed"
            result["manualReviewNamespace"] = f"pd-smoke-{session}-"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--image-a", default="")
    parser.add_argument("--image-b", default="")
    parser.add_argument("--authorize", default="")
    args = parser.parse_args(argv)
    try:
        if args.authorize != "disposable-local-docker" or not IMAGE.fullmatch(args.image_a) or not IMAGE.fullmatch(args.image_b):
            raise DockerError("explicit-disposable-authorization-and-preloaded-images-required")
        adapter = LocalDockerAdapter(LocalScope(args.endpoint, args.session, True))
        with host_lock():
            report = smoke(adapter, args.image_a, args.image_b)
    except DockerError as error:
        report = {"status": "not-performed", "reason": error.code}
    print(encode(report))
    return 0 if report["status"] == "passed" else 4


if __name__ == "__main__":
    sys.exit(main())
