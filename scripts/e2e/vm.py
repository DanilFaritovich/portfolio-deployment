"""Finite Multipass orchestration; no host Docker, shared mounts or VM deletion."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile

import yaml

from scripts.docker_adapter import DockerError, run_bounded
from scripts.e2e.safety import require, session_id

ROOT = Path(__file__).resolve().parents[2]


def cloud_init(session):
    session_id(session)
    initialize = f'''import json, pathlib, subprocess, os
os.umask(0o077)
machine = pathlib.Path('/etc/machine-id').read_text().strip()
marker = {{'session': '{session}', 'machine': machine}}
pathlib.Path('/etc/portfolio-e2e.json').write_text(json.dumps(marker))
root = pathlib.Path('/var/lib/portfolio-e2e/{session}')
root.mkdir(parents=True, mode=0o700)
daemon = subprocess.check_output(['docker', '--host', 'unix:///run/docker.sock', 'info', '--format', '{{{{.ID}}}}'], text=True).strip()
(root / 'identity.json').write_text(json.dumps(dict(marker, daemon=daemon)))
'''
    from scripts.e2e.gateway import configuration
    unit = '''[Unit]
Description=Disposable E2E loopback Caddy
After=docker.service
[Service]
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --config /etc/portfolio-e2e-caddy.json
Environment=HOME=/var/lib/caddy
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/caddy
[Install]
WantedBy=multi-user.target
'''
    return {"hostname": f"pd-e2e-{session}", "manage_etc_hosts": True,
        "write_files": [
            {"path": "/usr/sbin/policy-rc.d", "permissions": "0755", "content": "#!/bin/sh\nexit 101\n"},
            {"path": "/etc/portfolio-e2e-caddy.json", "content": json.dumps(configuration())},
            {"path": "/etc/systemd/system/portfolio-e2e-caddy.service", "content": unit},
            {"path": "/root/initialize-e2e.py", "permissions": "0600", "content": initialize}],
        "runcmd": [
            ["systemctl", "mask", "caddy.service"],
            ["apt-get", "update"],
            ["apt-get", "install", "-y", "docker.io", "docker-compose-v2", "caddy", "python3-venv"],
            ["systemctl", "enable", "--now", "docker"],
            ["systemctl", "daemon-reload"],
            ["python3", "/root/initialize-e2e.py"]]}


def run_host(argv, *, timeout=20):
    # Multipass needs its normal per-user client identity. Forward only these
    # host values, never Docker contexts, cloud credentials or SSH agents.
    binary = shutil.which("multipass")
    require(binary is not None, "multipass-not-installed")
    return run_bounded(["/usr/bin/env", f"HOME={Path.home()}", binary, *argv[1:]], timeout=timeout)


class VM:
    def __init__(self, session, run=run_host):
        self.session, self.run = session_id(session), run
        self.name = f"pd-e2e-{session}"

    def prerequisites(self):
        checks = {"multipass": shutil.which("multipass") is not None,
                  "linuxAmd64": sys.platform == "linux" and platform.machine() in ("x86_64", "amd64"),
                  "kvmDevice": Path("/dev/kvm").exists(),
                  "hostDisk20GiB": shutil.disk_usage(Path.home()).free >= 20 * 1024 ** 3}
        return {"status": "passed" if all(checks.values()) else "blocked", "checks": checks,
                "reason": "local-checks-only; authenticated Multipass service, VM architecture, network/package access checked during prepare"}

    def command(self, *args, timeout=20):
        return self.run(["multipass", *args], timeout=timeout)

    def info(self):
        doc = json.loads(self.command("info", self.name, "--format", "json"))
        info = doc.get("info", {}).get(self.name)
        require(info is not None and not info.get("mounts"), "unknown-vm-or-host-mounts")
        return info

    def guest(self, *args, timeout=60, initializing=False):
        info = self.info()
        require(info.get("state") == "Running", "e2e-vm-not-running")
        if not initializing:
            # Read-only identity check before every root guest operation. Never
            # adopt an existing instance merely because its name matches.
            check = ("import json,pathlib,stat,os; "
                     "p=pathlib.Path('/etc/portfolio-e2e.json'); i=p.lstat(); "
                     "assert stat.S_ISREG(i.st_mode) and i.st_uid==0 and not i.st_mode&0o077; "
                     "d=json.loads(p.read_text()); "
                     f"assert d['session']=='{self.session}'; "
                     "assert d['machine']==pathlib.Path('/etc/machine-id').read_text().strip(); "
                     f"assert pathlib.Path('/etc/hostname').read_text().strip()=='{self.name}'")
            self.command("exec", self.name, "--", "sudo", "--", "python3", "-c", check, timeout=10)
        return self.command("exec", self.name, "--", "sudo", "--", *args, timeout=timeout)

    def prepare(self, authorization):
        require(authorization == f"prepare:{self.session}", "explicit-vm-prepare-authorization-required")
        # Enumerate first: errors are unknown, never interpreted as absence.
        existing = json.loads(self.command("list", "--format", "json"))
        require(not any(i["name"] == self.name for i in existing["list"]), "vm-already-exists-no-adoption")
        with tempfile.TemporaryDirectory(prefix="pd-e2e-host-") as directory:
            cloud = Path(directory) / "cloud-init.yaml"
            cloud.write_text("#cloud-config\n" + yaml.safe_dump(cloud_init(self.session)))
            # Finite commands may exceed a minute; this CLI is for later operator use.
            self.command("launch", "24.04", "--name", self.name, "--cpus", "2", "--memory", "4G",
                         "--disk", "16G", "--cloud-init", str(cloud), timeout=900)
            self.guest("cloud-init", "status", "--wait", timeout=900, initializing=True)
            archive = Path(directory) / "source.tar"
            with tarfile.open(archive, "w") as tar:
                for folder, suffix in (("scripts", ".py"), ("schemas", ".json")):
                    for path in sorted((ROOT / folder).rglob("*" + suffix)):
                        require(path.resolve() == path, "source-symlinks-forbidden")
                        tar.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
                tar.add(ROOT / "requirements-dev.txt", arcname="requirements-dev.txt")
            self.command("transfer", str(archive), f"{self.name}:/tmp/portfolio-e2e-source.tar", timeout=60)
            self.guest("mkdir", "-m", "700", "/opt/portfolio-e2e")
            revision = run_bounded(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()
            source = Path(directory) / "source.json"
            source.write_text(json.dumps({"revision": revision, "archiveSha256": sha256(archive.read_bytes()).hexdigest()}))
            self.command("transfer", str(source), f"{self.name}:/tmp/pd-e2e-source.json")
            self.guest("install", "-m", "600", "/tmp/pd-e2e-source.json", "/opt/portfolio-e2e/source.json")
            self.guest("tar", "-xf", "/tmp/portfolio-e2e-source.tar", "-C", "/opt/portfolio-e2e")
            self.guest("python3", "-m", "venv", "/opt/portfolio-e2e/.venv")
            self.guest("/opt/portfolio-e2e/.venv/bin/pip", "install", "-r",
                       "/opt/portfolio-e2e/requirements-dev.txt", timeout=300)
        return {"status": "passed", "reason": "prepared-not-tested; next: e2e-images then separately authorized e2e-test", "vm": self.name}

    def invoke(self, action, authorization, gateway_authorization=""):
        require(authorization == f"{action}:{self.session}", "explicit-vm-action-authorization-required")
        require(action in ("run", "images", "clean", "diagnose"), "unknown-vm-action")
        return json.loads(self.guest("/opt/portfolio-e2e/.venv/bin/python", "/opt/portfolio-e2e/scripts/e2e_guest.py",
                            action, "--session", self.session, "--authorize", authorization,
                            "--gateway-authorize", gateway_authorization, timeout=3600))

    def transfer_images(self, lock):
        from scripts.e2e.images import Images
        images = Images(lock)  # Validate the entire contract offline BEFORE transfer.
        self.guest("mkdir", "-m", "700", "/opt/portfolio-e2e/images")
        paths = {Path(lock).absolute(), *(Path(lock).absolute().parent / r["manifest"] for r in
                    json.loads(Path(lock).read_text())["images"].values())}
        for path in sorted(paths):
            target = "lock.json" if path == Path(lock).absolute() else path.name
            require(target != "lock.json" or path == Path(lock).absolute(), "reserved-lock-filename")
            self.command("transfer", str(path), f"{self.name}:/tmp/pd-e2e-{target}")
            self.guest("install", "-m", "600", f"/tmp/pd-e2e-{target}", f"/opt/portfolio-e2e/images/{target}")
        return images


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prerequisites", "prepare", "status", "images", "test", "clean", "diagnose"))
    parser.add_argument("--session", default=os.environ.get("E2E_SESSION", ""))
    parser.add_argument("--authorize", default=os.environ.get("E2E_AUTHORIZE", ""))
    parser.add_argument("--gateway-authorize", default=os.environ.get("E2E_GATEWAY_AUTHORIZE", ""))
    parser.add_argument("--images", default=os.environ.get("E2E_IMAGES", ""))
    args = parser.parse_args(argv)
    try:
        vm = VM((args.session or "0" * 16) if args.action == "prerequisites" else args.session)
        if args.action == "prerequisites":
            result = vm.prerequisites()
        elif args.action == "prepare":
            result = vm.prepare(args.authorize)
        elif args.action == "status":
            result = {"status": "passed", "vm": vm.name, "info": vm.info(), "realE2E": "unknown-use-e2e-diagnose"}
        else:
            action = "run" if args.action == "test" else args.action
            require(args.authorize == f"{action}:{args.session}", "explicit-vm-action-authorization-required")
            if action == "run":
                require(args.gateway_authorize == f"activate:{args.session}", "explicit-gateway-authorization-required")
            if action == "images":
                vm.transfer_images(args.images)
            result = vm.invoke(action, args.authorize, args.gateway_authorize)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except (DockerError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": error.code if isinstance(error, DockerError) else
                          "e2e-prerequisite-or-state-invalid", "realE2E": "not-performed"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
