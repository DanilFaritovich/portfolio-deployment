"""Defaults and bounded anonymous metadata resolution; never pulls image layers."""
from hashlib import sha256
import json
import re
import urllib.request

from scripts.docker_adapter import DockerError, run_bounded
from scripts.e2e.fixture_guest import direct, platform_config
from scripts.e2e.host_state import Store
from scripts.e2e.safety import require
from scripts.e2e.vm import ROOT

REPOSITORY = "docker.io/library/python"
DIGEST = r"sha256:[0-9a-f]{64}"


def owner_from_origin(override="", *, run=run_bounded):
    identities = []
    for flags in (("--all",), ("--push", "--all")):
        try:
            raw = run(["git", "remote", "get-url", *flags, "origin"], cwd=str(ROOT)).decode()
        except (DockerError, OSError, UnicodeError):
            raise DockerError("unknown-github-origin") from None
        urls = raw.splitlines()
        require(len(urls) == 1, "ambiguous-github-origin")
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                             r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)/([A-Za-z0-9_.-]+?)(?:\.git)?", urls[0])
        require(match is not None and match[2] not in {".", ".."} and len(match[1]) <= 39, "unknown-github-origin")
        identities.append((match[1].lower(), match[2].lower()))
    require(identities[0] == identities[1], "ambiguous-github-origin")
    owner = override.lower() if override else identities[0][0]
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", owner) is not None and len(owner) <= 39,
            "invalid-fixture-ghcr-owner")
    return owner


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(newurl.startswith("https://"), "python-base-insecure-redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def fetch(url, headers=None):
    # No host credential helpers, Docker config, retries or registry error bodies.
    try:
        opener = urllib.request.build_opener(PublicRedirect())
        with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=20) as response:
            raw = response.read(65537)
        require(len(raw) <= 65536, "python-base-metadata-too-large")
        return raw
    except (OSError, ValueError):
        raise DockerError("python-base-registry-unavailable") from None


def verify_pin(record):
    require(isinstance(record, dict) and set(record) == {"source", "reference", "manifest", "config"},
            "invalid-python-base-pin")
    require(all(isinstance(value, str) for value in record.values()), "invalid-python-base-pin")
    require(re.fullmatch(re.escape(REPOSITORY) + "@" + DIGEST, record["reference"]) is not None,
            "invalid-python-base-pin")
    raw, config = record["manifest"].encode(), record["config"].encode()
    manifest = direct(raw)
    require(record["reference"] == REPOSITORY + "@sha256:" + sha256(raw).hexdigest() and
            manifest["config"]["digest"] == "sha256:" + sha256(config).hexdigest() and
            manifest["config"]["size"] == len(config), "python-base-digest-mismatch")
    platform_config(config)
    require(any(re.fullmatch(r"PYTHON_VERSION=3\.[0-9]+\.[0-9]+", item)
                for item in json.loads(config).get("config", {}).get("Env", [])), "python3-base-required")
    return record["reference"]


def resolve_base(directory, override="", *, get=fetch):
    try:
        return _resolve_base(directory, override, get=get)
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise DockerError("invalid-python-base-manifest-config-platform") from None


def _resolve_base(directory, override, *, get):
    configuration = json.loads((ROOT / "e2e/python-base.json").read_bytes())
    require(configuration == {"source": "docker.io/library/python:3.13-slim-bookworm",
                              "upstream": "https://github.com/docker-library/python", "platform": "linux/amd64"},
            "unreviewed-python-base-source")
    source = override or configuration["source"]
    require(source == configuration["source"] or re.fullmatch(re.escape(REPOSITORY) + "@" + DIGEST, source),
            "official-immutable-python-base-required")
    with Store(directory).locked() as store:
        pin = store.read("python-base.json")
        if pin is not None:
            reference = verify_pin(pin)
            require(source == pin["source"] or source == reference, "python-base-pin-changed-use-new-directory")
            return reference
        require(store.read("execution.json") is None, "fixture-execution-unfinished-no-retry")
        token = json.loads(get("https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"))["token"]
        require(isinstance(token, str) and 0 < len(token) <= 16384, "invalid-python-base-token")
        headers = {"Authorization": "Bearer " + token, "Accept": ", ".join((
            "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))}
        registry = "https://registry-1.docker.io/v2/library/python/"
        selector = source.split("@", 1)[1] if "@" in source else source.rsplit(":", 1)[1]
        raw = get(registry + "manifests/" + selector, headers)
        document = json.loads(raw)
        if "@" not in source and document.get("mediaType") in {
                "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}:
            require(document.get("schemaVersion") == 2 and isinstance(document.get("manifests"), list) and
                    len(document["manifests"]) <= 128, "invalid-python-base-index")
            children = [entry for entry in document["manifests"] if entry.get("platform") == {"os": "linux", "architecture": "amd64"}]
            require(len(children) == 1, "ambiguous-python-base-platform")
            child = children[0]
            require(re.fullmatch(DIGEST, child["digest"]) and type(child["size"]) is int and
                    0 < child["size"] <= 65536, "invalid-python-base-descriptor")
            raw = get(registry + "manifests/" + child["digest"], headers)
            require(len(raw) == child["size"] and "sha256:" + sha256(raw).hexdigest() == child["digest"],
                    "python-base-digest-mismatch")
        manifest = direct(raw)
        config = get(registry + "blobs/" + manifest["config"]["digest"], headers)
        reference = REPOSITORY + "@sha256:" + sha256(raw).hexdigest()
        require("@" not in source or source == reference, "python-base-digest-mismatch")
        pin = {"source": source, "reference": reference, "manifest": raw.decode(), "config": config.decode()}
        verify_pin(pin)
        store.write("python-base.json", pin)  # fsynced before any builder is created.
        return reference
