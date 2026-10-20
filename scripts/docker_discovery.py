"""Bounded Docker-only observations; never fabricate registry/gateway evidence."""

from dataclasses import dataclass
import json
import re
import time

from scripts.compose import LABEL
from scripts.docker_adapter import DockerError
from scripts.planning_inputs import load_observed_state
from scripts.reconciliation import encode

MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}


@dataclass(frozen=True)
class DockerResource:
    kind: str
    identifier: str
    name: str
    facts_json: str

    @property
    def facts(self):
        return json.loads(self.facts_json)


@dataclass(frozen=True)
class ImageEvidence:
    reference: str
    image_id: str
    platform: str
    manifest_digest: str | None
    verified: bool
    defaults_json: str


@dataclass(frozen=True)
class DockerObservation:
    resources: tuple[DockerResource, ...]
    images: tuple[ImageEvidence, ...]
    complete: bool
    reasons: tuple[str, ...] = ()

    def registry_observed(self):
        # Docker does not establish gateway route state, reference delivery,
        # application health, or completeness of the registry universe.
        return load_observed_state({"apiVersion": "portfolio.observed/v1",
                                    "scope": {"kind": "registry", "complete": False, "trusted": False},
                                    "applications": []})


def image_evidence(reference, document):
    descriptor = document.get("Descriptor") or {}
    selected = reference.split("@", 1)[-1]
    platform = f"{document.get('Os', '')}/{document.get('Architecture', '')}"
    verified = ("@sha256:" in reference and reference in document.get("RepoDigests", []) and
                descriptor.get("digest") == selected and descriptor.get("mediaType") in MANIFEST_TYPES and
                platform == "linux/amd64" and
                re.fullmatch(r"sha256:[0-9a-f]{64}", document.get("Id", "")) is not None)
    # RepoDigests and config ID alone cannot prove index-to-platform correspondence.
    return ImageEvidence(reference, document.get("Id", ""), platform, descriptor.get("digest"),
                         verified, encode(document.get("Config") or {}))


def discover(adapter, references):
    """All-resource inventory detects name/alias conflicts beyond project labels.

    Any read error invalidates the complete Docker observation, never means absent.
    Images must already exist; no inspection triggers a pull.
    """
    resources, images = [], []
    deadline = time.monotonic() + 60
    try:
        for kind in ("container", "network", "volume"):
            if time.monotonic() >= deadline:
                raise DockerError("discovery-deadline")
            for raw in adapter.inventory(kind):
                name = raw.get("Name", "").lstrip("/")
                identifier = raw.get("Id", name)
                if not name or not identifier:
                    raise DockerError("resource-identity-unavailable")
                if kind == "container":
                    config, host = raw.get("Config") or {}, raw.get("HostConfig") or {}
                    facts = {"labels": config.get("Labels") or {}, "state": {"Status": (raw.get("State") or {}).get("Status"),
                                       "Health": {"Status": ((raw.get("State") or {}).get("Health") or {}).get("Status")}},
                             "imageId": raw.get("Image"), "config": config, "host": host,
                             "networks": (raw.get("NetworkSettings") or {}).get("Networks") or {},
                             "mounts": raw.get("Mounts") or []}
                else:
                    facts = {"labels": raw.get("Labels") or {}, "driver": raw.get("Driver"),
                             "internal": raw.get("Internal"), "options": raw.get("Options") or {},
                             "scope": raw.get("Scope"), "containers": raw.get("Containers") or {}}
                resources.append(DockerResource(kind, identifier, name, encode(facts)))
        for reference in sorted(set(references)):
            if time.monotonic() >= deadline:
                raise DockerError("discovery-deadline")
            try:
                images.append(image_evidence(reference, adapter.inspect("image", reference)))
            except DockerError:
                # Availability cannot be established; preflight blocks this exact image.
                continue
        return DockerObservation(tuple(resources), tuple(images), True)
    except (DockerError, KeyError, TypeError, ValueError, UnicodeError):
        return DockerObservation((), (), False, ("docker-discovery-incomplete",))


def owned_resource(resource, expected_labels, project=None, identity=None):
    actual = resource.facts["labels"]
    if any(actual.get(k) != v for k, v in expected_labels.items()):
        return False
    if project is not None and actual.get("com.docker.compose.project") != project:
        return False
    key = {"container": "service", "network": "network", "volume": "volume"}[resource.kind]
    return identity is None or actual.get(f"com.docker.compose.{key}") == identity


def container_runtime(resource, artifact, service, image):
    """Conservative supported-runtime comparison against actual Engine inspect.

    Labels corroborate identity; they do not replace network/mount/image inspection.
    Unsupported overrides fail closed. Image defaults preserve command/entrypoint.
    """
    f, model = resource.facts, artifact.model
    wanted = model["services"][service]
    config, host = f["config"], f["host"]
    defaults = json.loads(image.defaults_json)
    if config.get("Image") != wanted["image"] or f["imageId"] != image.image_id or not image.verified:
        return False
    for key in ("Cmd", "Entrypoint", "Env", "User", "WorkingDir", "Healthcheck", "ExposedPorts", "Volumes"):
        if config.get(key) != defaults.get(key):
            return False
    if (host.get("Privileged") or host.get("PortBindings") or host.get("PublishAllPorts") or
        host.get("Binds") or host.get("CapAdd") or host.get("Devices") or host.get("DeviceRequests") or
        host.get("PidMode") or host.get("IpcMode") not in (None, "", "private") or
        host.get("NetworkMode") in ("host", "none") or host.get("VolumesFrom") or
        host.get("ExtraHosts") or host.get("Links") or host.get("SecurityOpt") or host.get("UsernsMode") or
        host.get("ReadonlyRootfs") or host.get("CapDrop") or host.get("Sysctls")):
        return False
    if host.get("NetworkMode") not in (None, "", model["networks"]["private"]["name"]):
        return False
    if any(host.get(key) for key in ("CpuShares", "CpuQuota", "CpusetCpus", "CpusetMems",
                                        "Memory", "MemoryReservation", "MemorySwap", "Ulimits",
                                        "Dns", "DnsSearch", "DnsOptions", "GroupAdd", "Init", "AutoRemove")):
        return False
    if host.get("RestartPolicy", {}).get("Name") not in (None, "", "no"):
        return False
    if host.get("Runtime") not in (None, "", "runc") or host.get("PidsLimit") not in (None, 0):
        return False
    networks = {model["networks"][n]["name"]: options for n, options in wanted["networks"].items()}
    if set(f["networks"]) != set(networks):
        return False
    for name, options in networks.items():
        aliases = set(f["networks"][name].get("Aliases") or [])
        required = set(options.get("aliases", []))
        allowed = required | {service, resource.name, resource.identifier[:12]}
        if not required <= aliases or aliases - allowed:
            return False
    mounts = {(m.get("Name"), m.get("Destination"), m.get("RW")) for m in f["mounts"] if m.get("Type") == "volume"}
    expected = {(model["volumes"][m["source"]]["name"], m["target"], True) for m in wanted.get("volumes", [])}
    return len(f["mounts"]) == len(mounts) and mounts == expected


class LocalStateProvider:
    """Honest integration boundary: re-admit desired; Docker cannot fill Stage 03."""
    def __init__(self, root, git_revision, adapter, *, previous_observed=None):
        self.root, self.git_revision, self.adapter = root, git_revision, adapter
        # Validated ObservedState supplies inspection hints only, never trust.
        self.previous_observed = previous_observed

    def refresh(self):
        from scripts.planning_inputs import build_desired_state
        desired = build_desired_state(self.root, self.git_revision)
        references = [c["image"] for app in desired.applications for c in app.spec["components"]]
        if self.previous_observed is not None:
            for app in self.previous_observed.document["applications"]:
                for component in app["components"]:
                    identity = component["imageIdentity"]
                    if component["existence"] == "present" and identity is not None:
                        references.append(identity["pullReference"])
        observation = discover(self.adapter, references)
        return desired, observation.registry_observed(), observation
