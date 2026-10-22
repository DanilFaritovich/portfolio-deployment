"""Offline six-image preparation bundle; separately opted-in execution lives in fixture_execution."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from scripts.e2e.host_state import Store
from scripts.e2e.image_lock import create
from scripts.e2e.images import Images, ROLES
from scripts.e2e.safety import require
from scripts.e2e.vm import ROOT
from scripts.planning_inputs import read_snapshot

ARGUMENTS = {
    "v1": ("v1", "http", "10001"), "v2": ("v2", "http", "10001"),
    "bad": ("bad", "bad", "10001"), "worker": ("worker", "worker", "10001"),
    "data1": ("v1", "data", "0"), "data2": ("v2", "data", "0"),
}


def source_bytes(path):
    require(path.absolute() == path.resolve(), "fixture-source-symlinks-forbidden")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 65536, "invalid-fixture-source")
        raw = stream.read(65537)
        require(len(raw) <= 65536, "fixture-source-too-large")
        return raw


def prepare(directory, owner, base, *, epoch=0, manifests=None):
    require(isinstance(owner, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", owner), "invalid-fixture-ghcr-owner")
    require(isinstance(base, str) and re.fullmatch(
        r"(?:docker\.io|ghcr\.io)/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}", base),
        "immutable-python-base-required")
    require(type(epoch) is int and 0 <= epoch <= 4102444800, "invalid-fixture-source-epoch")
    source = {name: source_bytes(ROOT / "e2e" / "images" / name) for name in ("Dockerfile", "app.py")}
    repositories = {role: f"ghcr.io/{owner}/pd-e2e-{role}" for role in sorted(ROLES)}
    plan = {"version": 1, "platform": "linux/amd64", "base": base, "epoch": epoch,
            "baseVerification": "operator-must-verify-direct-linux-amd64-Python-manifest; offline-plan-does-not-fetch-base",
            "toolchain": "record-builder-Buildah-Skopeo-versions; bit-identical-rebuild-not-guaranteed",
            "source": {name: sha256(raw).hexdigest() for name, raw in source.items()},
            "builder": {"requirement": "separate-disposable-builder; no-host-daemon; no-test-vm-credentials",
                        "execution": "separate-explicit-execute-opt-in", "publishAuthorization": "separate-explicit-operator-approval",
                        "permissions": "GHCR write:packages only; read access to fixture source; no repository write/admin"},
            "images": {role: {"repository": repositories[role],
                       "buildArgs": dict(zip(("VERSION", "MODE", "USER_ID"), ARGUMENTS[role]),
                                         PYTHON_BASE=base, SOURCE_DATE_EPOCH=str(epoch)),
                       "output": "direct-manifest; disable provenance/SBOM index wrapping",
                       "rawManifest": f"{role}.json"} for role in sorted(ROLES)}}
    with Store(directory).locked() as store:
        previous = store.read("plan.json")
        require(previous is None or previous == plan, "fixture-plan-changed-use-new-directory")
        context = store.path / "context"
        if previous is None:
            context.mkdir(mode=0o700)
            for name, raw in source.items():
                with (context / name).open("xb") as stream:
                    stream.write(raw)
                (context / name).chmod(0o600)
            store.write("repositories.json", repositories)
            store.write("plan.json", plan)
        else:
            require(all(source_bytes(context / name) == raw for name, raw in source.items()) and
                    store.read("repositories.json") == repositories, "fixture-source-or-repositories-changed")
        if manifests is None:
            return {"status": "blocked", "reason": "fixture-execution-requires-separate-opt-in",
                    "plan": str(store.path / "plan.json"), "sourceVerified": True,
                    "build": "not-performed", "publish": "not-performed", "lock": "not-created"}
        return import_manifests(store, repositories, manifests)


def import_manifests(store, repositories, manifests):
    # Import only exact producer-exported direct manifests. No registry or Engine I/O.
    raw_manifests = {role: read_snapshot(Path(manifests).absolute() / f"{role}.json") for role in sorted(ROLES)}
    output = store.path / "artifacts"
    if output.exists():
        Images(output / "lock.json")
        require(all(read_snapshot(output / f"{role}.json") == raw for role, raw in raw_manifests.items()),
                "fixture-manifests-changed-use-new-directory")
    else:
        staging = Path(tempfile.mkdtemp(prefix="import-", dir=store.path))
        for role, raw in raw_manifests.items():
            with (staging / f"{role}.json").open("xb") as stream:
                stream.write(raw)
            (staging / f"{role}.json").chmod(0o600)
        images = create(store.path / "repositories.json", staging)
        (staging / "lock.json").chmod(0o600)
        require(images.records == {role: repositories[role] + "@sha256:" + sha256(raw).hexdigest()
                                   for role, raw in raw_manifests.items()}, "fixture-lock-repository-mismatch")
        # Persist every artifact before atomically exposing the complete validated set.
        for path in staging.iterdir():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        staging.rename(output)
        os.fsync(store.fd)
    return {"status": "passed", "reason": "offline-lock-verified; publication-and-provenance-not-verified",
            "images": 6, "lock": str(output / "lock.json"), "build": "not-performed", "publish": "not-performed",
            "availability": "not-performed; public-GHCR-pull-and-Engine-corroboration-required-in-VM"}
