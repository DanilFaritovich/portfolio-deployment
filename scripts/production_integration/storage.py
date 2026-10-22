"""Read-only durable storage prerequisites; provisioning belongs to the operator."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from scripts.docker_adapter import DockerError


@dataclass(frozen=True)
class StorageBudgets:
    state: int
    journal: int
    gateway_config: int
    gateway_lkg: int
    certificates: int
    gateway_data: int
    temporary: int
    minimum_free: int

    def validate(self):
        values = tuple(self.__dict__.values())
        if any(type(n) is not int or not 1048576 <= n <= 10737418240 for n in values):
            raise DockerError('production-storage-budget-invalid')
        if sum(values) > 21474836480:
            raise DockerError('production-storage-total-budget-exceeded')


def validate_storage_directory(path, *, uid):
    """No mkdir/chown/chmod; reject symlinks, unsafe ancestors and foreign owner.

    This checks existing metadata, not application/gateway ownership. Independent
    bootstrap attestation must establish absence or manager ownership of content.
    """
    target = Path(path)
    if not target.is_absolute() or '..' in target.parts or str(target) != path:
        raise DockerError('production-storage-path-invalid')
    try:
        for parent in (*reversed(target.parents), target):
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022):
                raise DockerError('production-storage-ancestor-unsafe')
        info = target.lstat()
        if info.st_uid != uid or info.st_mode & 0o077:
            raise DockerError('production-storage-ownership-or-mode-invalid')
        if not os.access(target, os.R_OK | os.W_OK | os.X_OK):
            raise DockerError('production-storage-access-unavailable')
    except OSError:
        raise DockerError('production-storage-not-provisioned') from None
