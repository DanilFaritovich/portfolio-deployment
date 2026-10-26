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
INDEX_TYPES = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}


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


def _image_descriptor_matches(descriptor, digest, media_types):
    return (isinstance(descriptor, dict) and isinstance(digest, str) and
            re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None and
            descriptor.get('digest') == digest and isinstance(descriptor.get('mediaType'), str) and
            descriptor['mediaType'] in media_types and
            type(descriptor.get('size')) is int and 0 < descriptor['size'] <= 1024 * 1024 and
            ('platform' not in descriptor or descriptor['platform'] == {'os': 'linux', 'architecture': 'amd64'}))


def engine_image_id_matches(image, identifier, descriptor=None):
    """Legacy config ID or containerd target, using an already verified OCI map.

    Containerd returns the target digest in both image Id and Descriptor:
    https://github.com/moby/moby/blob/v28.0.4/daemon/containerd/image_inspect.go
    Never rewrite image.image_id: it remains the authenticated config digest.
    """
    if image.verified is not True or image.platform != 'linux/amd64':
        return False
    requested = image.reference.split('@')[-1]
    target_matches = (_image_descriptor_matches(descriptor, image.manifest_digest, MANIFEST_TYPES) or
                      (requested != image.manifest_digest and
                       _image_descriptor_matches(descriptor, requested, INDEX_TYPES)))
    if identifier == image.image_id:
        return descriptor is None or target_matches
    return target_matches and identifier == descriptor['digest']


def container_image_id_matches(image, identifier, descriptor=None):
    """Bind a container to the verified config or its selected platform manifest.

    ImageManifestDescriptor selects the actual platform when Image is an index
    digest. RepoDigests, matching defaults or an index ID alone are insufficient.
    """
    if image.verified is not True or image.platform != 'linux/amd64':
        return False
    manifest_matches = _image_descriptor_matches(descriptor, image.manifest_digest, MANIFEST_TYPES)
    if identifier == image.image_id:
        return descriptor is None or manifest_matches
    return manifest_matches and identifier in (image.manifest_digest, image.reference.split('@')[-1])


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


def discover(adapter, references, *, resolve_image=image_evidence):
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
                    if 'ImageManifestDescriptor' in raw:
                        facts['imageManifestDescriptor'] = raw['ImageManifestDescriptor']
                else:
                    facts = {"labels": raw.get("Labels") or {}, "driver": raw.get("Driver"),
                             "internal": raw.get("Internal"), "options": raw.get("Options") or {},
                             "scope": raw.get("Scope"), "containers": raw.get("Containers") or {},
                             "ipam": raw.get("IPAM"), "attachable": raw.get("Attachable"),
                             "ingress": raw.get("Ingress"), "enableIPv6": raw.get("EnableIPv6")}
                resources.append(DockerResource(kind, identifier, name, encode(facts)))
        for reference in sorted(set(references)):
            if time.monotonic() >= deadline:
                raise DockerError("discovery-deadline")
            try:
                images.append(resolve_image(reference, adapter.inspect("image", reference)))
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
    """Compatible boolean facade over the shared structured runtime policy."""
    return container_runtime_verification(resource, artifact, service, image).matched


def container_runtime_verification(resource, artifact, service, image):
    from scripts.runtime_contract import verify_runtime
    return verify_runtime(resource, artifact, service, image)


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
