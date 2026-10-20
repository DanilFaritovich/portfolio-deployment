"""Test-only VM identity and fixed execution policy. No host fallback."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import time

from scripts.docker_adapter import DockerError, LocalScope, LocalDockerAdapter, run_bounded

ENDPOINT = "unix:///run/docker.sock"
BASE = Path("/var/lib/portfolio-e2e")


def require(condition, code):
    if not condition:
        raise DockerError(code)


def session_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16}", value), "invalid-e2e-session")
    return value


def private_json(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "unsafe-e2e-path")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
            not info.st_mode & 0o077 and info.st_nlink == 1 and info.st_size <= 65536, "unsafe-e2e-state")
    return json.loads(path.read_bytes())


@dataclass(frozen=True)
class GuestScope:
    session: str
    authorization: str
    machine: str
    daemon: str

    def validate(self, *, timeout=2):
        session_id(self.session)
        require(self.authorization == f"run:{self.session}", "explicit-e2e-run-authorization-required")
        require(re.fullmatch(r"[0-9a-f]{32}", self.machine or "") and
                re.fullmatch(r"[A-Za-z0-9:-]{1,128}", self.daemon or ""), "invalid-vm-identity")
        require(os.geteuid() == 0, "guest-root-runner-required")
        require(not any(k in os.environ for k in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
                                                 "DOCKER_CERT_PATH", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME")),
                "docker-environment-forbidden")
        require(Path("/etc/hostname").read_text().strip() == f"pd-e2e-{self.session}", "wrong-vm-hostname")
        require(Path("/etc/machine-id").read_text().strip() == self.machine, "wrong-vm-machine-id")
        marker = private_json("/etc/portfolio-e2e.json")
        require(marker == {"session": self.session, "machine": self.machine}, "wrong-vm-marker")
        require(run_bounded(["/usr/bin/systemd-detect-virt"], timeout=timeout).strip() in (b"kvm", b"qemu", b"microsoft", b"apple"),
                "disposable-vm-required")
        require('ID=ubuntu' in Path("/etc/os-release").read_text() and
                'VERSION_ID="24.04"' in Path("/etc/os-release").read_text(), "ubuntu-2404-required")
        LocalScope(ENDPOINT, self.session, True).validate()

    @property
    def directory(self):
        return BASE / self.session


class GuestDocker(LocalDockerAdapter):
    """Same Stage 05 Compose mutations, with VM and daemon identity rechecked every call."""
    def __init__(self, scope):
        self.up_calls = []
        self.deadline = None
        self.guest = scope
        scope.validate()
        super().__init__(LocalScope(ENDPOINT, scope.session, True))

    def _run(self, args, *, timeout=20, cwd=None):
        def remaining():
            value = min(timeout, self.deadline - time.monotonic()) if self.deadline else timeout
            require(value > 0, "probe-budget-exhausted")
            return value
        self.guest.validate(timeout=min(2, remaining()))
        info = json.loads(super()._run(["info", "--format", "{{json .}}"], timeout=min(10, remaining())))
        require(info.get("ID") == self.guest.daemon and info.get("Name") == f"pd-e2e-{self.test_session}",
                "wrong-vm-daemon")
        return super()._run(args, timeout=remaining(), cwd=cwd)

    @contextmanager
    def budget(self, timeout):
        before = self.deadline
        self.deadline = min(before, time.monotonic() + timeout) if before else time.monotonic() + timeout
        try:
            yield
        finally:
            self.deadline = before

    def up(self, artifact, service):
        self.up_calls.append((artifact.project, service))
        return super().up(artifact, service)
