"""Production policy over the existing flock implementation; never provisions paths."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from scripts.deployment import host_lock, owns_host_lock
from scripts.docker_adapter import DockerError

CANONICAL_LOCK_DIRECTORY = '/srv/portfolio/locks'
CANONICAL_LOCK = CANONICAL_LOCK_DIRECTORY + '/host.lock'


def validate_lock_path(path=CANONICAL_LOCK):
    if path != CANONICAL_LOCK:
        raise DockerError('production-lock-path-mismatch')
    target = Path(path)
    try:
        for parent in reversed(target.parents):
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or
                info.st_mode & 0o022):
                raise DockerError('production-lock-parent-unsafe')
        directory = target.parent.lstat()
        info = target.lstat()
        if (directory.st_uid != os.geteuid() or directory.st_mode & 0o077 or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_mode & 0o077 or info.st_nlink != 1):
            raise DockerError('production-lock-metadata-unsafe')
    except OSError:
        raise DockerError('production-lock-not-provisioned') from None


def require_production_lease(lease):
    validate_lock_path()
    try:
        actual = os.fstat(lease.fd)
        expected = os.lstat(CANONICAL_LOCK)
        if (not owns_host_lock(lease) or lease.path != CANONICAL_LOCK or
            (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
            raise ValueError
    except (OSError, AttributeError, TypeError, ValueError):
        raise DockerError('production-canonical-lock-ownership-required') from None


@contextmanager
def production_host_lock():
    validate_lock_path()  # no fallback, creation or migration in this interface
    with host_lock(CANONICAL_LOCK_DIRECTORY) as lease:
        require_production_lease(lease)
        yield lease
