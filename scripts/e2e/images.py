"""Separate E2E immutable GHCR contract; production admission stays unchanged."""
from hashlib import sha256
import json
from pathlib import Path
import re

from scripts.docker_discovery import ImageEvidence, MANIFEST_TYPES
from scripts.e2e.safety import require
from scripts.planning_inputs import parse_snapshot, read_snapshot
from scripts.reconciliation import encode

ROLES = {"v1", "v2", "bad", "worker", "data1", "data2"}


class Images:
    def __init__(self, path):
        path = Path(path).absolute()
        doc = parse_snapshot(read_snapshot(path))
        require(set(doc) == {"version", "images"} and doc["version"] == 1 and set(doc["images"]) == ROLES,
                "invalid-e2e-image-lock")
        self.records, self.manifests = {}, {}
        for role, record in doc["images"].items():
            require(set(record) == {"reference", "manifest"}, "invalid-e2e-image-record")
            ref, filename = record["reference"], record["manifest"]
            require(isinstance(ref, str) and re.fullmatch(
                r"ghcr\.io/[a-z0-9]+(?:-[a-z0-9]+)*/pd-e2e-[a-z0-9-]+@sha256:[0-9a-f]{64}", ref),
                "test-only-ghcr-repository-required")
            require(isinstance(filename, str) and re.fullmatch(r"[a-z0-9-]+\.json", filename), "invalid-manifest-path")
            raw = read_snapshot(path.parent / filename)
            manifest = parse_snapshot(raw)
            require("sha256:" + sha256(raw).hexdigest() == ref.split("@")[1], "manifest-content-digest-mismatch")
            require(manifest.get("schemaVersion") == 2 and manifest.get("mediaType") in MANIFEST_TYPES and
                    isinstance(manifest.get("config"), dict) and
                    re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["config"].get("digest", "")) and
                    isinstance(manifest.get("layers"), list) and len(manifest["layers"]) <= 128,
                    "direct-oci-manifest-required")
            config = manifest["config"]
            require(config.get("mediaType") in {"application/vnd.oci.image.config.v1+json",
                    "application/vnd.docker.container.image.v1+json"} and
                    type(config.get("size")) is int and 0 < config["size"] <= 2 * 1024 * 1024,
                    "invalid-oci-config-descriptor")
            require(all(isinstance(layer, dict) and
                        re.fullmatch(r"sha256:[0-9a-f]{64}", layer.get("digest", "")) and
                        type(layer.get("size")) is int and 0 <= layer["size"] <= 4 * 1024 ** 3 and
                        layer.get("mediaType") in {"application/vnd.oci.image.layer.v1.tar",
                            "application/vnd.oci.image.layer.v1.tar+gzip", "application/vnd.oci.image.layer.v1.tar+zstd",
                            "application/vnd.docker.image.rootfs.diff.tar.gzip"} for layer in manifest["layers"]),
                    "invalid-oci-layer-descriptor")
            self.records[role] = ref
            self.manifests[ref] = manifest
        require(len(set(self.records.values())) == len(ROLES), "distinct-e2e-images-required")

    def evidence(self, reference, engine):
        manifest = self.manifests.get(reference)
        require(manifest is not None, "image-outside-e2e-lock")
        platform = f"{engine.get('Os')}/{engine.get('Architecture')}"
        verified = (platform == "linux/amd64" and reference in engine.get("RepoDigests", []) and
                    manifest["config"]["digest"] == engine.get("Id"))
        return ImageEvidence(reference, engine.get("Id", ""), platform, reference.split("@")[1],
                             verified, encode(engine.get("Config") or {}))
