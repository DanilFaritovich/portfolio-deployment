"""Single-use, separately confirmed disposable fixture builder and publisher VMs."""
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shutil

import yaml

from scripts.docker_adapter import DockerError
from scripts.e2e.fixture_bundle import prepare, source_bytes, import_manifests
from scripts.e2e.fixture_guest import digest_file, unpack
from scripts.e2e.host_state import Store
from scripts.e2e.control import guard, preflight
from scripts.e2e.images import Images, ROLES
from scripts.e2e.image_lock import create
from scripts.e2e.orchestration import confirm
from scripts.e2e.safety import require
from scripts.e2e.vm import ROOT, VM
from scripts.e2e.multipass import evidence

GUEST_ROOT = "/var/lib/pd-fixtures"


class FixtureVM(VM):
    def __init__(self, session, kind):
        super().__init__(session)
        require(kind in ("builder", "publisher"), "invalid-fixture-vm-kind")
        self.kind = kind
        self.name = f"pd-fixture-{kind}-{session}"
        self.pinned = None

    def prerequisites(self):
        result = super().prerequisites()
        result["checks"]["hostDisk64GiB"] = shutil.disk_usage(Path.home()).free >= 64 * 1024 ** 3
        result["status"] = "passed" if all(result["checks"].values()) else "blocked"
        return result

    def guest(self, *args, timeout=60, initializing=False):
        info = self.info()
        require(info.get("state") == "Running", "fixture-vm-not-running")
        if not initializing:
            check = ("import json,pathlib,platform,stat; "
                     "p=pathlib.Path('/etc/pd-fixture.json'); s=p.lstat(); "
                     "assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077; "
                     "d=json.loads(p.read_text()); "
                     f"assert d['session']=='{self.session}' and d['kind']=='{self.kind}'; "
                     f"assert pathlib.Path('/etc/hostname').read_text().strip()=='{self.name}'; "
                     "assert d['machine']==pathlib.Path('/etc/machine-id').read_text().strip(); "
                     "assert platform.machine()=='x86_64'; print(d['machine'])")
            identity = self.checked_exec("identity", "python3", "-c", check, timeout=10).decode().strip()
            require(self.pinned is None or identity == self.pinned, "fixture-vm-identity-changed")
            self.pinned = identity
        return self.checked_exec("cloud-init" if initializing else "exec", *args, timeout=timeout)

    def transfer(self, path, name):
        require(name in {"plan.json", "Dockerfile", "app.py", "fixture_guest.py", "images.tar"}, "invalid-fixture-transfer")
        self.guest("true")  # Recheck ownership and no mounts before every transfer.
        self.upload(path, f"/tmp/pd-fixture-{name}", timeout=600)
        destination = GUEST_ROOT + ("/context/" if name in {"Dockerfile", "app.py"} else "/") + name
        self.guest("install", "-m", "600", f"/tmp/pd-fixture-{name}", destination)

    def prepare_fixture(self, directory):
        existing = self.inventory()
        require(not any(item["name"] == self.name for item in existing["list"]), "fixture-vm-exists-no-adoption")
        marker = ("import pathlib,json,os; os.umask(0o077); "
                  f"pathlib.Path('/etc/pd-fixture.json').write_text(json.dumps(dict(session='{self.session}',kind='{self.kind}',"
                  "machine=pathlib.Path('/etc/machine-id').read_text().strip())))")
        cloud = {"hostname": self.name, "manage_etc_hosts": True,
                 "packages": ["buildah", "skopeo"] if self.kind == "builder" else ["skopeo"],
                 "runcmd": [["mkdir", "-p", GUEST_ROOT + "/context"],
                            ["chmod", "700", GUEST_ROOT], ["python3", "-c", marker]]}
        path = directory / (self.kind + "-cloud-init.yaml")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as out:
            out.write("#cloud-config\n" + yaml.safe_dump(cloud))
        path.chmod(0o600)
        staged, _ = self.staging().copy_to(path)
        self.command("launch", "24.04", "--name", self.name, "--cpus", "2", "--memory", "4G", "--disk", "24G",
                     "--cloud-init", str(staged), timeout=900)
        self.guest("cloud-init", "status", "--wait", timeout=900, initializing=True)
        self.transfer(directory / "plan.json", "plan.json")
        self.transfer(directory / "fixture_guest.py", "fixture_guest.py")
        if self.kind == "builder":
            for name in ("Dockerfile", "app.py"):
                self.transfer(directory / "context" / name, name)
        return self.pinned

    def build(self, directory):
        result = json.loads(self.guest("python3", GUEST_ROOT + "/fixture_guest.py", "build", self.session, timeout=7200))
        require(set(result) == {"archive", "toolchain"}, "fixture-build-result-invalid")
        target = directory / "images.tar"
        require(not target.exists(), "fixture-artifact-exists")
        self.guest("install", "-o", "ubuntu", "-g", "ubuntu", "-m", "600", GUEST_ROOT + "/images.tar", "/home/ubuntu/pd-fixture-images.tar", timeout=600)
        expected = self.remote_fingerprint("/home/ubuntu/pd-fixture-images.tar")
        require(expected["sha256"] == result["archive"], "fixture-archive-digest-mismatch")
        self.staging().receive(self.command, f"{self.name}:/home/ubuntu/pd-fixture-images.tar",
                               target, result["archive"], size=expected["size"])
        require(digest_file(target) == result["archive"], "fixture-archive-digest-mismatch")
        return result

    def verify_public(self):
        return json.loads(self.guest("python3", GUEST_ROOT + "/fixture_guest.py", "verify-public", self.session, timeout=1800))

    def publish(self, directory):
        self.transfer(directory / "images.tar", "images.tar")
        return json.loads(self.guest("python3", GUEST_ROOT + "/fixture_guest.py", "publish", self.session, timeout=7200))


def execute(directory, owner, base, *, epoch=0, factory=FixtureVM, consent=confirm):
    preflight()
    prepare(directory, owner, base, epoch=epoch)  # Offline validation before registration.
    with guard(directory, "fixtures"):
        return _execute(directory, owner, base, epoch=epoch, factory=factory, consent=consent)


def _execute(directory, owner, base, *, epoch=0, factory=FixtureVM, consent=confirm):
    prepare(directory, owner, base, epoch=epoch)  # Verify frozen plan/source before any VM access.
    with Store(directory).locked() as store:
        state = store.read("execution.json")
        if state is not None:
            require(state.get("status") == "passed" and state.get("phase") == "complete", "fixture-execution-unfinished-no-retry")
            images = Images(store.path / "artifacts/lock.json")
            require(images.records == state["references"], "published-fixture-lock-changed")
            return state
        require(not (store.path / "artifacts").exists(), "offline-import-not-publication-use-new-directory")
        pinned_plan = store.read("plan.json")
        session = secrets.token_hex(8)
        builder, publisher = factory(session, "builder"), factory(session, "publisher")
        prerequisites = builder.prerequisites()
        if prerequisites["status"] != "passed":
            return {**prerequisites, "reason": "fixture-host-prerequisites-blocked",
                    "missing": [name for name, passed in prerequisites.get("checks", {}).items() if not passed]}
        payload = source_bytes(ROOT / "scripts/e2e/fixture_guest.py")
        with os.fdopen(os.open("fixture_guest.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=store.fd), "wb") as out:
            out.write(payload)
        (store.path / "fixture_guest.py").chmod(0o600)
        state = {"version": 1, "session": session, "builder": builder.name, "publisher": publisher.name,
                 "phase": "planned", "status": "planned", "identities": {}, "references": {},
                 "payloadSha256": sha256(payload).hexdigest()}
        store.write("execution.json", state)
        print(json.dumps({"session": session, "builder": builder.name, "publisher": publisher.name,
                          "receipt": str(store.path / "execution.json")}), flush=True)
        try:
            for phase, message in (
                ("builder-prepare", "Create NEW disposable builder Ubuntu VM (2 CPU / 4 GiB / 24 GiB); install Buildah/Skopeo, no credentials or Docker daemon."),
                ("build", "Verify pinned source/base/platform and build/export six direct OCI images inside builder VM."),
                ("publisher-prepare", "Create NEW isolated publisher Ubuntu VM (2 CPU / 4 GiB / 24 GiB); install Skopeo, no Docker daemon."),
                ("publish", f"Publish six images to GHCR and verify exact manifest bytes with publisher authentication.\n"
                 f"In a SEPARATE terminal run: multipass shell {publisher.name}\n"
                 "Inside publisher only: sudo skopeo login --authfile /run/pd-fixtures-auth.json ghcr.io\n"
                 "Use a short-lived write:packages PAT. "
                 "Never enter credentials in this terminal. Publisher removes only the temporary auth file after the attempt."),
                ("verify-public", "Make ALL six GHCR packages public in GitHub package settings now; then verify exact manifests/configs anonymously. No push is repeated."),
            ):
                machine = builder if phase in ("builder-prepare", "build") else publisher
                if phase == "publish":
                    message += "\nTarget repositories:\n" + "\n".join(
                        pinned_plan["images"][role]["repository"] for role in sorted(ROLES))
                consent(f"Phase: {phase}; session: {session}; VM: {machine.name}\n{message}", f"{phase}:{session}")
                plan = store.read("plan.json")
                require(plan == pinned_plan, "fixture-plan-changed")
                require(sha256(source_bytes(store.path / "fixture_guest.py")).hexdigest() == state["payloadSha256"],
                        "fixture-payload-changed")
                require(all(sha256(source_bytes(store.path / "context" / name)).hexdigest() == digest
                            for name, digest in plan["source"].items()), "fixture-source-changed")
                state.update(phase=phase, status="in-progress")
                store.write("execution.json", state)  # Durable intent, including before VM creation/transfer.
                if phase.endswith("prepare"):
                    machine = builder if phase == "builder-prepare" else publisher
                    state["identities"][machine.kind] = machine.prepare_fixture(store.path)
                elif phase == "build":
                    result = builder.build(store.path)
                    state["build"] = result
                    manifests = unpack(store.path / "images.tar", store.path / "exported")
                    raw = store.path / "raw"
                    raw.mkdir(mode=0o700)
                    for role, content in manifests.items():
                        with os.fdopen(os.open(raw / (role + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
                            stream.write(content)
                        (raw / (role + ".json")).chmod(0o600)
                    create(store.path / "repositories.json", raw)  # Validate candidate contract before any publication.
                    (raw / "lock.json").chmod(0o600)
                else:
                    require(digest_file(store.path / "images.tar") == state["build"]["archive"], "fixture-archive-changed")
                    result = publisher.publish(store.path) if phase == "publish" else publisher.verify_public()
                    require(set(result) == {"references"} and result["references"] == {
                        role: plan["images"][role]["repository"] + "@sha256:" +
                        sha256(source_bytes(store.path / "raw" / (role + ".json"))).hexdigest() for role in sorted(ROLES)},
                        "published-manifests-differ")
                    state["references"] = result["references"]
                state["status"] = "planned"
                store.write("execution.json", state)
            state.update(phase="lock", status="in-progress")
            store.write("execution.json", state)
            result = import_manifests(store, store.read("repositories.json"), store.path / "raw")
            require(Images(result["lock"]).records == state["references"], "published-fixture-lock-changed")
            state.update(phase="complete", status="passed", lock=result["lock"])
            store.write("execution.json", state)
            return state
        except BaseException as error:
            state["status"] = "unknown" if state["status"] == "in-progress" else "blocked"
            state.update(evidence(error))
            state["reason"] = error.code if isinstance(error, DockerError) else "fixture-interrupted-or-incomplete"
            store.write("execution.json", state)
            if isinstance(error, (KeyboardInterrupt, Exception)):
                return state
            raise
