"""Pure, deterministic Compose projection of admitted Stage 03 values."""

from dataclasses import dataclass
import hashlib
import json
import re

from scripts.reconciliation import PlanningInputError, encode

LABEL = "io.github.danilfaritovich.portfolio-deployment"


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def labels(application, component=None):
    return {f"{LABEL}.managed": "true", f"{LABEL}.application": application,
            **({f"{LABEL}.component": component} if component else {})}


@dataclass(frozen=True)
class ComposeArtifact:
    application: str
    project: str
    model_json: str

    @property
    def model(self):
        return json.loads(self.model_json)

    @property
    def sha256(self):
        return digest(self.model)

    def to_json(self):
        return self.model_json + "\n"


def render_compose(application, *, test_session=None):
    """Trusted-value API: use build_desired_state for schema/global/admission checks.

    Test sessions change all resource identities and cannot select production names.
    Git revision is deliberately absent from runtime labels/configuration.
    """
    if test_session is not None and not re.fullmatch(r"[0-9a-f]{16}", test_session):
        raise PlanningInputError("invalid-test-session")
    prefix = f"pd-smoke-{test_session}" if test_session else "portfolio"
    project = f"{prefix}-{application.id}"
    spec = application.spec
    model = {"name": project, "services": {}, "networks": {
        "private": {"name": f"{project}-private", "internal": True,
                    "labels": labels(application.id)}}}
    for c in spec["components"]:
        service = {"image": c["image"], "platform": c["platform"],
                   "networks": {"private": {}}, "labels": labels(application.id, c["name"])}
        if test_session:
            service["labels"][f"{LABEL}.test-session"] = test_session
        if c["dependsOn"]:
            service["depends_on"] = {name: {"condition": "service_started"} for name in c["dependsOn"]}
        if c["proxyNetwork"]:
            alias = f"{application.id}-{c['name']}"
            if test_session:
                alias = f"{test_session}-{alias}"
            service["networks"]["proxy"] = {"aliases": [alias]}
            model["networks"]["proxy"] = {"name": f"{prefix}-proxy", "external": True}
        if c["volumeMounts"]:
            service["volumes"] = [{"type": "volume", "source": m["volume"], "target": m["mountPath"],
                                   "volume": {"nocopy": True}} for m in c["volumeMounts"]]
        # Includes declarative health/dependencies, but never invents an in-image probe.
        service["labels"][f"{LABEL}.runtime"] = digest(c)
        model["services"][c["name"]] = service
    if spec["storage"]["type"] == "persistent":
        model["volumes"] = {v["name"]: {"name": f"{project}-{v['name']}", "labels": labels(application.id)}
                            for v in spec["storage"]["volumes"]}
    if test_session:
        for section in ("networks", "volumes"):
            for resource in model.get(section, {}).values():
                if not resource.get("external"):
                    resource["labels"][f"{LABEL}.test-session"] = test_session
    return ComposeArtifact(application.id, project, encode(model))


def startup_order(application):
    pending = {c["name"]: set(c["dependsOn"]) for c in application.spec["components"]}
    result = []
    while pending:
        ready = sorted(n for n, deps in pending.items() if not deps - set(result))
        if not ready:
            raise PlanningInputError("dependency-cycle-or-missing")
        for name in ready:
            result.append(name)
            del pending[name]
    return tuple(result)
