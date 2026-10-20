"""Generate a separate admitted test registry; never write the real policy."""
from pathlib import Path
import yaml

from scripts.e2e.safety import require
from scripts.planning_inputs import build_desired_state

APPLICATIONS = ("e2e-app", "e2e-sentinel", "e2e-data", "e2e-unknown", "e2e-partial",
                "e2e-intent", "e2e-mutation", "e2e-readiness", "e2e-conflict")


def create_registry(root, images):
    root = Path(root)
    root.mkdir(mode=0o700)  # Never adopt/rewrite a previous run's registry.
    (root / "registry/applications").mkdir(parents=True)
    (root / "policy").mkdir()
    policy = {"apiVersion": "portfolio.deployment/v1", "kind": "AdmissionPolicy", "applications": []}
    for app in APPLICATIONS:
        components = [{"name": name, "image": images.records["worker" if name == "worker" else "v1"],
                       "platform": "linux/amd64", "type": "worker" if name == "worker" else "http",
                       "health": {"strategy": "imageHealthcheck"} if name == "worker" else
                                 {"strategy": "http", "path": "/health"},
                       **({"port": 8080} if name != "worker" else {})}
                      for name in ("backend", "frontend", "worker")]
        persistent = app == "e2e-data"
        if persistent:
            components[0]["image"] = images.records["data1"]
            components[0]["volumeMounts"] = [{"volume": "data", "mountPath": "/data"}]
        spec = {"enabled": True, "components": components,
                "storage": {"type": "persistent", "volumes": [{"name": "data"}]} if persistent else {"type": "stateless"}}
        if app in ("e2e-app", "e2e-sentinel"):
            spec["ingress"] = {"hostname": f"{app}.example.test", "component": "frontend", "port": 8080}
        doc = {"apiVersion": "portfolio.deployment/v1", "kind": "Application",
               "metadata": {"id": app, "sourceRepository": "fixtures/local-e2e"}, "spec": spec}
        (root / f"registry/applications/{app}.yaml").write_text(yaml.safe_dump(doc))
        policy["applications"].append({"id": app, "sourceRepository": "fixtures/local-e2e",
            "imageRepositories": {c["name"]: sorted({r.split("@")[0] for r in images.records.values()}) for c in components},
            "hostnames": [spec["ingress"]["hostname"]] if "ingress" in spec else [], "platforms": ["linux/amd64"]})
    (root / "policy/admission.yaml").write_text(yaml.safe_dump(policy))
    return build_desired_state(root, "a" * 40)


def change_image(root, app, image, component="backend"):
    require(app in APPLICATIONS and component in ("backend", "frontend", "worker"), "unknown-e2e-fixture")
    path = Path(root) / f"registry/applications/{app}.yaml"
    doc = yaml.safe_load(path.read_text())
    next(c for c in doc["spec"]["components"] if c["name"] == component)["image"] = image
    path.write_text(yaml.safe_dump(doc))
    # Re-run full schema, semantic and separate test admission validation.
    return build_desired_state(root, "a" * 40)
