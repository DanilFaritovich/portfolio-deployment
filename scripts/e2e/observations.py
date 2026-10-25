"""Complete observations only for the bounded disposable registry and VM session.

Registry is re-admitted each round. Image/config, routes and health are independent
live sources; journal is deliberately not accepted by this interface.
"""
from dataclasses import replace
import time
import uuid

from scripts.compose import LABEL, digest, labels, render_compose
from scripts.composite_state import CompositeSample, CompositeStateProvider, SourceEvidence
from scripts.docker_discovery import discover, container_runtime, owned_resource
from scripts.e2e.safety import require
from scripts.e2e.transports import container_address
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.reconciliation import encode


def owner(app, component=None):
    return {"manager": "portfolio-deployment", "application": app,
            **({"component": component} if component else {})}


class Reader:
    def __init__(self, root, revision, adapter, images, gateway, probes, *, local_scope=None):
        self.root, self.revision, self.adapter = root, revision, adapter
        self.images, self.gateway, self.probes = images, gateway, probes
        self.endpoints, self.actual = {}, {}
        self.local_scope = local_scope

    def __call__(self):
        if self.local_scope is None:
            self.adapter.guest.validate()
        else:
            from scripts.docker_adapter import LocalScope
            require(type(self.local_scope) is LocalScope and self.local_scope is self.adapter.scope,
                    'explicit-disposable-local-scope-required')
            self.local_scope.validate()
        desired = build_desired_state(self.root, self.revision)
        require(0 < len(desired.applications) <= 16, "bounded-test-registry-required")
        require(all(a.id.startswith("e2e-") and a.source_repository == "fixtures/local-e2e" and
                    not a.spec["configRefs"] and not a.spec["secretRefs"] and
                    all(c["image"] in self.images.manifests and not c["configRefs"] and not c["secretRefs"]
                        for c in a.spec["components"]) for a in desired.applications), "test-registry-scope-required")
        round_id = uuid.uuid4().hex
        sources = []
        def evidence(name):
            sources.append(SourceEvidence(name, round_id, time.monotonic(), True, True))
        docker = discover(self.adapter, self.images.manifests, resolve_image=self.images.evidence)
        require(docker.complete and len(docker.images) == len(self.images.manifests) and
                all(i.verified for i in docker.images), "test-image-evidence-incomplete")
        evidence("docker")
        catalog = {a.id: a for a in desired.applications}
        for resource in docker.resources:
            ls = resource.facts["labels"]
            if ls.get(f"{LABEL}.test-session") == self.adapter.test_session and resource.kind == "container":
                require(ls.get(f"{LABEL}.application") in catalog, "unknown-session-application")
        images = {i.reference: i for i in docker.images}
        applications, endpoints, actual = [], {}, {}
        health = []
        for app in desired.applications:
            spec = app.spec
            components, actual_components = [], []
            artifact = render_compose(app, test_session=self.adapter.test_session)
            candidates = [r for r in docker.resources if r.kind == "container" and
                          (r.facts["labels"].get("com.docker.compose.project") == artifact.project or
                           r.facts["labels"].get(f"{LABEL}.application") == app.id or
                           r.name.startswith(artifact.project + "-"))]
            require(all(r.facts["labels"].get("com.docker.compose.service") in
                        {c["name"] for c in spec["components"]} for r in candidates), "unknown-session-component")
            for component in spec["components"]:
                matches = [r for r in candidates if r.facts["labels"].get("com.docker.compose.service") == component["name"]]
                require(len(matches) <= 1, "ambiguous-component-identity")
                if not matches:
                    continue
                resource = matches[0]
                require(owned_resource(resource, {**labels(app.id, component["name"]),
                            f"{LABEL}.test-session": self.adapter.test_session}, artifact.project, component["name"]),
                        "foreign-component-ownership")
                current = dict(component, image=resource.facts["config"].get("Image"))
                image = images.get(current["image"])
                actual_spec = app.spec
                actual_spec["components"] = [current if c["name"] == current["name"] else c for c in actual_spec["components"]]
                current_app = replace(app, spec_json=encode(actual_spec))
                require(image is not None and resource.facts["labels"].get(f"{LABEL}.runtime") == digest(current) and
                        container_runtime(resource, render_compose(current_app, test_session=self.adapter.test_session),
                                          current["name"], image), "effective-test-runtime-unverified")
                raw = self.adapter.inspect("container", resource.identifier)
                endpoints[(app.id, current["name"])] = container_address(raw, self.adapter, app.id)
                # Health source is collected separately after runtime/gateway sampling.
                health.append((current, resource.identifier, components))
                runtime = {k: v for k, v in current.items() if k not in ("name", "image")}
                components.append({"name": current["name"], "existence": "present", "owner": owner(app.id, current["name"]),
                                   "runtime": runtime, "runtimeComplete": True, "health": "unknown",
                                   "imageIdentity": {"pullReference": image.reference, "pullKind": "manifest",
                                       "manifestDigest": image.manifest_digest, "platform": image.platform, "relationship": "verified"}})
                actual_components.append(current)
            actual_spec = app.spec
            actual_spec["components"] = actual_components
            if len(actual_components) == len(spec["components"]):
                actual[app.id] = replace(app, spec_json=encode(actual_spec))
            # Storage is accepted only with the executor's independent resource
            # preflight (driver, options, ownership and attachment checks).
            from scripts.deployment import preflight_resources
            require(not preflight_resources(app, artifact, docker), "test-resource-preflight-failed")
            applications.append({"id": app.id, "owner": owner(app.id), "existence": "present" if components else "absent",
                                 "components": components, "storage": spec["storage"] if components else None,
                                 "configRefs": [] if components else None, "secretRefs": [] if components else None,
                                 "route": {"existence": "absent", "owner": owner(app.id), "configuration": None}})
        evidence("runtime")
        routes, gateway_verified = self.gateway.observations(desired, endpoints)
        for app in applications:
            if app["id"] in routes:
                require(app["existence"] == "present", "active-route-without-application")
                app["route"].update(existence="present", configuration=routes[app["id"]])
        evidence("gateway")
        for component, identity, components in health:
            result = self.probes.probe(component, identity, timeout=2, follow_redirects=False)
            good = (result.running is True and (result.health == "healthy" if component["type"] == "worker" else
                    result.http_status is not None and 200 <= result.http_status < 300 and not result.redirected))
            next(c for c in components if c["name"] == component["name"])["health"] = "healthy" if good else "degraded"
        evidence("readiness")
        # Close container replacement/configuration and inventory races across rounds.
        after = discover(self.adapter, self.images.manifests, resolve_image=self.images.evidence)
        def identity(d):
            return tuple((r.kind, r.identifier, r.name, encode({k: v for k, v in r.facts.items() if k != "state"}))
                         for r in d.resources)
        require(after.complete and identity(after) == identity(docker) and after.images == docker.images,
                "docker-changed-during-observation")
        self.endpoints, self.actual = endpoints, actual
        observed = load_observed_state({"apiVersion": "portfolio.observed/v1",
                    "scope": {"kind": "registry", "complete": True, "trusted": True}, "applications": applications})
        return CompositeSample(desired, observed, docker, tuple(sources), gateway_verified)

    def provider(self):
        return CompositeStateProvider(self, max_age=30)
