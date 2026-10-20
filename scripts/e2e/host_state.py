"""Private, bounded host receipts with a persistent whole-operation flock."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat

from scripts.deployment_journal import private_directory
from scripts.e2e.safety import require
from scripts.planning_inputs import parse_snapshot


class Store:
    def __init__(self, path):
        self.path = Path(path).absolute()

    @contextmanager
    def locked(self):
        with private_directory(self.path) as directory:
            self.fd = directory
            lock = os.open("controller.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                self.check(os.fstat(lock))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    require(False, "e2e-controller-busy")
                yield self
            finally:
                os.close(lock)  # Never unlink the lock inode.

    @staticmethod
    def check(info):
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size <= 65536,
                "unsafe-e2e-host-state")

    def read(self, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            self.check(os.fstat(stream.fileno()))
            raw = stream.read(65537)
            require(len(raw) <= 65536, "e2e-host-state-too-large")
            return parse_snapshot(raw)

    def write(self, name, value):
        # A leftover pending file is uncertainty, never permission to overwrite.
        raw = json.dumps(value, sort_keys=True).encode()
        require(len(raw) <= 65536, "e2e-host-state-too-large")
        try:
            self.check(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
        except FileNotFoundError:
            pass
        pending = name + ".pending"
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(pending, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)
