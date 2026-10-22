"""Retained, no-follow, session isolated exchange with the Snap client.

Credentials are never staging inputs. Each operation owns an exclusive directory;
no cleanup or adoption is performed, even after a failed access probe.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import stat

from scripts.e2e.paths import directory as checked_directory
from scripts.deployment_journal import JournalError
from scripts.docker_adapter import DockerError
from scripts.e2e.safety import require, session_id
from scripts.e2e.multipass import MultipassError, run

MAX_ARTIFACT = 4 * 1024 ** 3


@contextmanager
def directory(path):
    try:
        with checked_directory(path) as descriptor:
            yield descriptor
    except JournalError:
        require(False, "e2e-staging-unsafe-directory")
    except DockerError as error:
        if error.code.startswith("e2e-path-"):
            require(False, "e2e-staging-unsafe-directory")
        raise


def check_file(info, *, private):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
            not info.st_mode & (0o077 if private else 0o022) and info.st_size <= MAX_ARTIFACT,
            "e2e-staging-unsafe-file")


@contextmanager
def source(path, *, private=False):
    path = Path(path).absolute()
    require(".." not in path.parts, "e2e-staging-traversal")
    # Pin every ancestor and refuse symlinks; source checkout may be readable.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    system_uid = os.fstat(fd).st_uid
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            require(info.st_uid in (system_uid, os.geteuid()) and
                    (not info.st_mode & 0o022 or info.st_uid == system_uid and info.st_mode & stat.S_ISVTX),
                    "e2e-staging-unsafe-source-parent")
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            check_file(os.fstat(stream.fileno()), private=private)
            yield stream
    except OSError:
        require(False, "e2e-staging-unsafe-file")
    finally:
        os.close(fd)


def fingerprint(stream):
    digest, size = sha256(), 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        require(size <= MAX_ARTIFACT, "e2e-staging-size-limit")
        digest.update(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def copy_bounded(stream, output):
    count = 0
    while chunk := stream.read(1024 * 1024):
        count += len(chunk)
        require(count <= MAX_ARTIFACT, "e2e-staging-size-limit")
        output.write(chunk)


def snap_probe(path, expected):
    """Read exact bytes from the same confinement as the Multipass client."""
    snap = shutil.which("snap")
    require(snap is not None, "multipass-snap-probe-unavailable")
    # Fixed script; path is shell quoted, no host environment/credentials sent.
    script = ("set -eu\n" + "test -r " + shlex.quote(str(path)) + "\n" +
              ": >> " + shlex.quote(str(path)) + "\n" +
              "wc -c < " + shlex.quote(str(path)) + "\n" +
              "sha256sum -- " + shlex.quote(str(path)) + "\nexit\n").encode()
    require(len(script) <= 4096, "multipass-staging-path-too-long")
    try:
        raw = run([snap, "run", "--shell", "multipass"], timeout=20,
                  operation="staging-access", input_data=script)
        lines = raw.decode("ascii").splitlines()
        require(len(lines) == 2 and int(lines[0]) == expected["size"] and
                lines[1].split()[0] == expected["sha256"], "multipass-staging-integrity-failed")
    except (MultipassError, ValueError, UnicodeError, IndexError):
        raise MultipassError("staging-access", "denied-check-snap-home-permission") from None


class Staging:
    def __init__(self, session, role, probe=None):
        self.session = session_id(session)
        require(role in ("e2e", "builder", "publisher"), "e2e-staging-invalid-role")
        self.role, self.probe = role, probe or snap_probe
        home = Path.home()
        require(home.is_absolute() and not any(p.startswith(".") for p in home.parts), "e2e-staging-home-hidden")
        self.root = home / "PortfolioDeploymentE2E"

    @contextmanager
    def operation(self):
        with directory(self.root) as root_fd:
            try:
                os.mkdir(self.session, 0o700, dir_fd=root_fd)
                os.fsync(root_fd)
            except FileExistsError:
                pass
        parent = self.root / self.session
        with directory(parent) as parent_fd:
            name = self.role + "-" + secrets.token_hex(12)
            os.mkdir(name, 0o700, dir_fd=parent_fd)  # Collision fails, never reused.
            os.fsync(parent_fd)
        with directory(parent / name) as descriptor:
            yield parent / name, descriptor

    def copy_to(self, path, name=None):
        path = Path(path)
        name = path.name if name is None else name
        require(name in {"cloud-init.yaml", "builder-cloud-init.yaml", "publisher-cloud-init.yaml",
                             "source.tar", "source.json", "plan.json", "Dockerfile", "app.py",
                             "fixture_guest.py", "images.tar", "lock.json", "manifest.json"} or
                name in {"v1.json", "v2.json", "bad.json", "worker.json", "data1.json", "data2.json"},
                "e2e-staging-file-not-allowlisted")
        with self.operation() as (area, fd):
            target = area / name
            with source(path) as stream:
                before = os.fstat(stream.fileno())
                expected = fingerprint(stream)
                stream.seek(0)
                out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(out, "wb") as output:
                    copy_bounded(stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                after = os.fstat(stream.fileno())
                require(all(getattr(before, k) == getattr(after, k) for k in
                            ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")),
                        "e2e-staging-source-changed")
            with source(target, private=True) as staged:
                require(fingerprint(staged) == expected, "e2e-staging-copy-integrity")
            self.record(fd, {"direction": "out", "file": name, **expected})
            self.probe(target, expected)
            return target, expected

    @staticmethod
    def record(fd, value):
        out = os.open("receipt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)

    def receive(self, transfer, remote, target, digest, size=None):
        target = Path(target)
        require(target.name == "images.tar", "e2e-staging-invalid-download")
        with self.operation() as (area, fd):
            # Precreate destination privately; Multipass writes an existing file.
            out = os.open("images.tar", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            os.close(out)
            staged = area / "images.tar"
            self.record(fd, {"direction": "in", "file": "images.tar", "expectedSha256": digest, "expectedSize": size})
            self.probe(staged, {"size": 0, "sha256": sha256(b"").hexdigest()})
            transfer("transfer", remote, str(staged), timeout=600)
            with source(staged, private=True) as stream:
                expected = fingerprint(stream)
                require(expected["sha256"] == digest and (size is None or expected["size"] == size),
                        "fixture-archive-digest-mismatch")
                with directory(target.parent) as target_fd:
                    out = os.open(target.name, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600, dir_fd=target_fd)
                    with os.fdopen(out, "wb") as output:
                        stream.seek(0)
                        copy_bounded(stream, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.fsync(target_fd)
            with source(target, private=True) as copied:
                require(fingerprint(copied) == expected, "e2e-staging-copy-integrity")
        return expected
