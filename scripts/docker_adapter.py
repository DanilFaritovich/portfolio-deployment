"""Narrow local-only CLI boundary. No implicit context, pulls, shell or raw logs."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import tempfile
import time

from scripts.compose import ComposeArtifact
from scripts.compose_version import CLI_REQUIREMENTS, supported_compose, supported_compose_cli
from scripts.planning_inputs import parse_snapshot

MAX_OUTPUT = 1024 * 1024


class DockerError(RuntimeError):
    def __init__(self, code, unknown=False):
        self.code, self.unknown = code, unknown
        super().__init__(code)


def command_failure(returncode, errors):
    """Closed classification only; raw Docker diagnostics never leave transport."""
    if b'permission denied' in errors.lower():
        return DockerError('docker-permission-denied')
    return DockerError('docker-command-failed', returncode < 0 or returncode >= 128 or
                      any(token in errors.lower() for token in
                          (b'cannot connect', b'connection refused', b'error during connect',
                           b'eof', b'connection reset', b'context deadline', b'broken pipe',
                           b'i/o timeout', b'transport is closing')))


def run_bounded(argv, *, timeout=20, cwd=None):
    """Drain both pipes with a hard combined cap; diagnostics never leave adapter."""
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "HOME": cwd or "/nonexistent",
                   "DOCKER_CONFIG": cwd or "/nonexistent", "COMPOSE_DISABLE_ENV_FILE": "1"}
    try:
        with subprocess.Popen(argv, shell=False, env=environment, cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            data, errors, total = bytearray(), bytearray(), 0
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    selector.register(process.stderr, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise DockerError("docker-timeout", True)
                        for key, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > MAX_OUTPUT:
                                raise DockerError("docker-output-limit", True)
                            if key.fileobj is process.stdout:
                                data.extend(chunk)
                            else:
                                errors.extend(chunk)
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                if process.returncode:
                    raise command_failure(process.returncode, errors)
                return bytes(data)
            except BaseException:
                process.kill()
                process.wait()
                raise
    except subprocess.TimeoutExpired:
        raise DockerError("docker-timeout", True) from None
    except OSError:
        raise DockerError("docker-unavailable", True) from None


def verify_socket(endpoint):
    if not re.fullmatch(r"unix:///[^\s]+", endpoint or ""):
        raise DockerError("local-unix-endpoint-required")
    path = Path(endpoint[7:])
    # Resolve canonical path; symlinks and parent traversal are not endpoint aliases.
    if str(path.resolve()) != str(path) or not stat.S_ISSOCK(path.stat().st_mode):
        raise DockerError("invalid-local-socket")
    return str(path)


@dataclass(frozen=True)
class LocalScope:
    endpoint: str
    session: str
    authorized: bool = False

    def validate(self):
        if not self.authorized or not re.fullmatch(r"[0-9a-f]{16}", self.session):
            raise DockerError("disposable-session-authorization-required")
        try:
            return verify_socket(self.endpoint)
        except OSError:
            raise DockerError("local-socket-unavailable") from None


class LocalDockerAdapter:
    """Stage 05 real mutations are restricted to an explicitly selected smoke session."""
    def __init__(self, scope):
        self.scope = scope
        self.test_session = scope.session
        scope.validate()
        self.binary = shutil.which("docker")
        if not self.binary:
            raise DockerError("docker-not-installed")

    def _run(self, args, *, timeout=20, cwd=None):
        self.scope.validate()
        with tempfile.TemporaryDirectory(prefix="pd-cli-") as home:
            return run_bounded([self.binary, "--host", self.scope.endpoint, *args],
                               timeout=timeout, cwd=cwd or home)

    def inventory(self, kind):
        commands = {"container": ["container", "ls", "--all", "--quiet", "--no-trunc"],
                    "network": ["network", "ls", "--quiet", "--no-trunc"],
                    "volume": ["volume", "ls", "--quiet"]}
        if kind not in commands:
            raise DockerError("unsupported-resource-kind")
        ids = self._run(commands[kind]).decode("ascii").split()
        if len(ids) > 512 or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", i) for i in ids):
            raise DockerError("inventory-limit-or-invalid-identity")
        records, size = [], 0
        deadline = time.monotonic() + 30
        for identity in ids:
            if time.monotonic() >= deadline:
                raise DockerError("inventory-deadline")
            record = self.inspect(kind, identity)
            size += len(json.dumps(record))
            if size > 4 * MAX_OUTPUT:
                raise DockerError("inventory-byte-limit")
            records.append(record)
        return tuple(records)

    def inspect(self, kind, identity):
        if kind not in ("container", "network", "volume", "image") or not identity or identity.startswith("-"):
            raise DockerError("invalid-inspection")
        try:
            result = parse_snapshot(self._run([kind, "inspect", identity]))
            if len(result) != 1 or not isinstance(result[0], dict):
                raise ValueError
            return result[0]
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise DockerError("invalid-docker-observation") from None

    def _artifact(self, artifact):
        if not isinstance(artifact, ComposeArtifact) or artifact.project != f"pd-smoke-{self.scope.session}-{artifact.application}":
            raise DockerError("wrong-test-project")
        # Requiring an artifact generated by the trusted renderer is an internal API boundary.
        model = artifact.model
        if model.get("name") != artifact.project:
            raise DockerError("wrong-test-project")
        for section in ("networks", "volumes"):
            if any(not item["name"].startswith(f"pd-smoke-{self.scope.session}-") for item in model.get(section, {}).values()):
                raise DockerError("resource-outside-test-namespace")

    def prerequisites(self):
        try:
            version = self._run(["compose", "version", "--short"]).decode("ascii").strip()
            if not supported_compose(version):
                raise DockerError("compose-version-unsupported")
            if not supported_compose_cli([self._run(list(args)).decode("utf-8")
                                          for args, _ in CLI_REQUIREMENTS]):
                raise DockerError("compose-capabilities-unsupported")
            server = json.loads(self._run(["version", "--format", "{{json .Server}}"]))
            if server.get("Os") != "linux" or server.get("Arch") != "amd64":
                raise DockerError("unsupported-daemon-platform")
        except (ValueError, UnicodeError, AttributeError):
            raise DockerError("docker-prerequisites-unavailable") from None

    def authorize(self, artifact):
        self.scope.validate()
        self._artifact(artifact)
        self.prerequisites()

    def up(self, artifact, service):
        self.authorize(artifact)
        if service not in artifact.model["services"]:
            raise DockerError("unknown-service")
        scoped_compose_up(artifact, service, self._run)

    # Cleanup is intentionally absent from the ordinary execution interface.


def scoped_compose_up(artifact, service, run):
    """Shared fixed Stage 05 config/up sequence; callers supply scoped transports."""
    with tempfile.TemporaryDirectory(prefix="pd-compose-") as directory:
        path = Path(directory) / "compose.json"
        path.write_text(artifact.to_json())
        argv = ["compose", "--project-name", artifact.project, "--project-directory", directory,
                "--env-file", "/dev/null", "--file", str(path)]
        run([*argv, "config", "-q"], cwd=directory)
        run([*argv, "up", "-d", "--no-deps", "--no-build", "--pull", "never", service],
            timeout=60, cwd=directory)


def official_validate(artifact):
    """Syntax only, no socket connection. Missing Compose is explicitly not-performed."""
    binary = shutil.which("docker")
    if not binary:
        return {"status": "not-performed", "reason": "docker-not-installed"}
    try:
        with tempfile.TemporaryDirectory(prefix="pd-config-") as directory:
            path = Path(directory) / "compose.json"
            path.write_text(artifact.to_json())
            try:
                version = run_bounded([binary, "compose", "version", "--short"], cwd=directory).decode("ascii").strip()
                if not supported_compose(version):
                    return {"status": "not-performed", "reason": "compose-version-unsupported"}
            except (DockerError, UnicodeError):
                return {"status": "not-performed", "reason": "compose-plugin-unavailable"}
            if not supported_compose_cli([run_bounded([binary, *args], cwd=directory).decode("utf-8")
                                          for args, _ in CLI_REQUIREMENTS]):
                return {"status": "failed", "reason": "compose-capabilities-unsupported"}
            run_bounded([binary, "--host", "unix:///nonexistent/pd-no-daemon.sock", "compose",
                         "--project-name", artifact.project, "--project-directory", directory,
                         "--env-file", "/dev/null", "--file", str(path), "config", "-q"], cwd=directory)
        return {"status": "passed", "reason": "syntax-only"}
    except UnicodeError:
        return {"status": "failed", "reason": "compose-capabilities-unsupported"}
    except DockerError as error:
        return {"status": "not-performed" if error.code == "docker-unavailable" else "failed",
                "reason": error.code}


class MockDockerAdapter:
    """Explicit offline command recorder; never calls subprocess or opens a socket."""
    def __init__(self, session, failures=()):
        if not re.fullmatch(r"[0-9a-f]{16}", session):
            raise DockerError("invalid-test-session")
        self.test_session = session
        self.failures = iter(failures)
        self._calls = []

    @property
    def calls(self):
        return tuple(self._calls)

    def authorize(self, artifact):
        if artifact.project != f"pd-smoke-{self.test_session}-{artifact.application}":
            raise DockerError("wrong-test-project")

    def up(self, artifact, service):
        self.authorize(artifact)
        if service not in artifact.model["services"]:
            raise DockerError("unknown-service")
        self._calls.append((artifact.project, service))
        error = next(self.failures, None)
        if error:
            raise error
