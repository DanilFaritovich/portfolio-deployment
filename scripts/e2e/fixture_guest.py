"""Standalone disposable builder/publisher payload. No host or E2E daemon access."""
from hashlib import sha256
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import selectors
import time
import sys
import tarfile

ROLES = {"v1", "v2", "bad", "worker", "data1", "data2"}
ROOT = Path("/var/lib/pd-fixtures")
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C"}


def need(condition):
    if not condition:
        raise ValueError("fixture-contract-invalid")


def command(*args, timeout=900):
    # Raw errors may include registry authentication details: never return them.
    with subprocess.Popen(args, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        output = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    need(time.monotonic() < deadline)
                    for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        need(len(output) <= 1024 * 1024)
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            need(process.returncode == 0)
            return bytes(output)
        except BaseException:
            process.kill()
            process.wait()
            raise


def receipt(root, name, value):
    with (root / name).open("x") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def platform_config(raw):
    need(len(raw) <= 2 * 1024 * 1024)
    config = json.loads(raw)
    need(config.get("os") == "linux" and config.get("architecture") == "amd64")


def direct(raw):
    need(len(raw) <= 65536)
    manifest = json.loads(raw)
    need(manifest.get("schemaVersion") == 2 and manifest.get("mediaType") in
         {MANIFEST, "application/vnd.docker.distribution.manifest.v2+json"} and
         isinstance(manifest.get("layers"), list) and len(manifest["layers"]) <= 128)
    need(0 < manifest["config"]["size"] <= 2 * 1024 * 1024)
    for descriptor in [manifest["config"], *manifest["layers"]]:
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor["digest"]) and
             type(descriptor["size"]) is int and 0 <= descriptor["size"] <= 4 * 1024 ** 3)
    return manifest


def digest_file(path):
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_directory(path):
    raw = (path / "manifest.json").read_bytes()
    need(len(raw) <= 65536)
    manifest = direct(raw)
    for descriptor in [manifest["config"], *manifest["layers"]]:
        blob = path / descriptor["digest"].split(":")[1]
        need(blob.is_file() and not blob.is_symlink() and blob.stat().st_size == descriptor["size"] and
             digest_file(blob) == descriptor["digest"].split(":")[1])
    platform_config((path / manifest["config"]["digest"].split(":")[1]).read_bytes())
    return raw


def unpack(archive, target):
    # Never tar.extract untrusted paths, links, devices, duplicate names or sparse files.
    need(not archive.is_symlink() and archive.absolute() == archive.resolve() and
         archive.is_file() and archive.stat().st_size <= 4 * 1024 ** 3)
    target.mkdir(mode=0o700)
    seen, total = set(), 0
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            need(len(seen) < 1024 and member.name not in seen and member.isfile() and not member.sparse and
                 re.fullmatch(r"(?:v1|v2|bad|worker|data1|data2)/(?:manifest\.json|version|[0-9a-f]{64})", member.name))
            total += member.size
            need(0 <= member.size and total <= 4 * 1024 ** 3)
            seen.add(member.name)
            path = target / member.name
            path.parent.mkdir(mode=0o700, exist_ok=True)
            with tar.extractfile(member) as source, path.open("xb") as out:
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
            path.chmod(0o600)
    need({name.split("/")[0] for name in seen} == ROLES)
    return {role: validate_directory(target / role) for role in sorted(ROLES)}


def build(plan, root=ROOT):
    receipt(root, "build.intent.json", {"status": "in-progress"})
    need(set(plan["images"]) == ROLES and set(plan["source"]) == {"Dockerfile", "app.py"})
    for name, digest in plan["source"].items():
        need(name in {"Dockerfile", "app.py"} and digest_file(root / "context" / name) == digest)
    base = "docker://" + plan["base"]
    raw = command("skopeo", "inspect", "--retry-times=0", "--raw", base)
    need("sha256:" + sha256(raw).hexdigest() == plan["base"].split("@")[1])
    direct(raw)  # Refuse indexes even if Skopeo could select a child platform.
    config = command("skopeo", "inspect", "--retry-times=0", "--config", "--raw", base)
    need("sha256:" + sha256(config).hexdigest() == json.loads(raw)["config"]["digest"])
    need(len(config) == json.loads(raw)["config"]["size"])
    platform_config(config)
    (root / "base-manifest.json").write_bytes(raw)
    versions = {tool: command(tool, "--version").decode().strip() for tool in ("buildah", "skopeo")}
    (root / "toolchain.json").write_text(json.dumps(versions))
    output = root / "output"
    output.mkdir(mode=0o700)
    for role, record in sorted(plan["images"].items()):
        receipt(root, role + "-build.intent.json", {"status": "in-progress"})
        tag = "localhost/pd-e2e-" + role
        arguments = [arg for key, value in sorted(record["buildArgs"].items()) for arg in ("--build-arg", key + "=" + value)]
        command("buildah", "bud", "--isolation=chroot", "--platform=linux/amd64", "--format=docker",
                "--timestamp=" + str(plan["epoch"]), "--retry=0", "--pull=missing", "--layers=false", "--rm=false", "--force-rm=false", "-t", tag,
                *arguments, str(root / "context"))
        check_container = command("buildah", "from", "--pull=never", tag).decode().strip()
        need(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", check_container))
        command("buildah", "run", "--isolation=chroot", check_container, "--", "python3", "-c",
                "import os,pathlib; " + f"assert os.geteuid()=={int(record['buildArgs']['USER_ID'])}; " +
                "compile(pathlib.Path('/fixture/app.py').read_bytes(), '/fixture/app.py', 'exec')")
        command("buildah", "push", "--retry=0", "--compression-format=gzip", "--force-compression", "--format=v2s2", tag, "dir:" + str(output / role))
        raw = validate_directory(output / role)
        config = json.loads((output / role / json.loads(raw)["config"]["digest"].split(":")[1]).read_bytes())
        need(config.get("config", {}).get("Healthcheck", {}).get("Test") == ["CMD", "python3", "/fixture/app.py", "healthcheck"])
        receipt(root, role + "-build.result.json", {"manifest": "sha256:" + sha256(raw).hexdigest()})
    with tarfile.open(root / "images.tar", "x:") as tar:
        for role in sorted(ROLES):
            for path in sorted((output / role).iterdir()):
                need(path.is_file() and not path.is_symlink())
                tar.add(path, arcname=role + "/" + path.name, recursive=False)
    return {"archive": digest_file(root / "images.tar"), "toolchain": versions}


def publish(plan, session, root=ROOT):
    receipt(root, "publish.intent.json", {"status": "in-progress"})
    auth = Path("/run/pd-fixtures-auth.json")
    info = auth.lstat()
    need(auth.is_file() and not auth.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o077)
    # The operator logs in directly inside this publisher VM. Nothing reads the credential on the host.
    try:
        manifests = unpack(root / "images.tar", root / "publish-input")
        refs = {}
        for role, raw in manifests.items():
            receipt(root, role + "-publish.intent.json", {"status": "in-progress"})
            repository = plan["images"][role]["repository"]
            need(re.fullmatch(r"ghcr\.io/[a-z0-9]+(?:-[a-z0-9]+)*/pd-e2e-" + role, repository))
            ref = repository + "@sha256:" + sha256(raw).hexdigest()
            command("skopeo", "copy", "--retry-times=0", "--preserve-digests", "--authfile", str(auth),
                    "dir:" + str(root / "publish-input" / role), "docker://" + repository + ":fixture-" + session)
            remote = command("skopeo", "inspect", "--retry-times=0", "--authfile", str(auth), "--raw", "docker://" + ref)
            need(remote == raw)
            refs[role] = ref
            (root / (role + ".published.json")).write_bytes(remote)
            receipt(root, role + "-publish.result.json", {"reference": ref})
        return {"references": refs}
    finally:
        auth.unlink()  # Only ephemeral credentials; artifacts and operation evidence are retained.


def verify_public(plan, root=ROOT):
    receipt(root, "verify-public.intent.json", {"status": "in-progress"})
    empty = root / "anonymous.json"
    with empty.open("x") as stream:
        stream.write('{"auths":{}}')
    refs = {}
    for role in sorted(ROLES):
        raw = validate_directory(root / "publish-input" / role)
        ref = plan["images"][role]["repository"] + "@sha256:" + sha256(raw).hexdigest()
        remote = command("skopeo", "inspect", "--retry-times=0", "--authfile", str(empty), "--raw", "docker://" + ref)
        need(remote == raw)
        config = command("skopeo", "inspect", "--retry-times=0", "--authfile", str(empty), "--config", "--raw", "docker://" + ref)
        need("sha256:" + sha256(config).hexdigest() == json.loads(raw)["config"]["digest"])
        platform_config(config)
        refs[role] = ref
        (root / (role + ".public.json")).write_bytes(remote)
    return {"references": refs}


if __name__ == "__main__":
    os.umask(0o077)
    try:
        action, session = sys.argv[1:]
        need(re.fullmatch(r"[0-9a-f]{16}", session))
        marker = json.loads(Path("/etc/pd-fixture.json").read_bytes())
        kind = "builder" if action == "build" else "publisher"
        need(marker["session"] == session and marker["kind"] == kind and
             marker["machine"] == Path("/etc/machine-id").read_text().strip() and
             Path("/etc/hostname").read_text().strip() == f"pd-fixture-{kind}-{session}" and
             platform.machine() == "x86_64" and not Path("/run/docker.sock").exists())
        plan = json.loads((ROOT / "plan.json").read_bytes())
        result = build(plan) if action == "build" else publish(plan, session) if action == "publish" else verify_public(plan) if action == "verify-public" else None
        need(result is not None)
        print(json.dumps(result, sort_keys=True))
    except BaseException:
        # No tracebacks, commands, login material or registry response bodies in receipts.
        print('{"status":"unknown","reason":"fixture-guest-operation-incomplete"}')
        sys.exit(2)
