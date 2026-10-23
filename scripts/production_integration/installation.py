"""Root-owned sealed installation contract, checked before request processing."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from scripts.docker_adapter import DockerError

BUNDLE = Path('/opt/portfolio-deployment')


def protected(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise DockerError('production-installation-path-invalid')
    try:
        for parent in (*reversed(path.parents), path):
            info = parent.lstat()
            is_directory = parent != path or directory
            if (info.st_uid != 0 or info.st_mode & 0o022 or
                (is_directory and not stat.S_ISDIR(info.st_mode)) or
                (not is_directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
                raise DockerError('production-installation-metadata-unsafe')
    except OSError:
        raise DockerError('production-installation-not-provisioned') from None


@dataclass(frozen=True)
class SealedInstallation:
    manager_sha: str
    files: tuple[tuple[str, str], ...]

    def verify(self):
        if (os.geteuid() != 0 or not sys.flags.isolated or not sys.flags.ignore_environment or
            not sys.flags.no_user_site or not sys.flags.no_site or not sys.dont_write_bytecode):
            raise DockerError('production-root-isolated-context-required')
        if not re.fullmatch(r'[0-9a-f]{40}', self.manager_sha):
            raise DockerError('production-installed-manager-sha-invalid')
        protected(BUNDLE, directory=True)
        if (BUNDLE / '.git').exists():
            raise DockerError('production-working-checkout-forbidden')
        pins = dict(self.files)
        if len(pins) != len(self.files) or not pins:
            raise DockerError('production-installation-inventory-invalid')
        for path, digest in self.files:
            protected(path)
            if not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise DockerError('production-installation-pin-invalid')
            with open(path, 'rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != digest:
                raise DockerError('production-installation-content-changed')
        # Every installed source/dependency/extension is inventoried. Interpreter,
        # stdlib and system tool/plugin trees are infrastructure-owned pins too.
        for path in BUNDLE.rglob('*'):
            if path.is_dir():
                protected(path, directory=True)
            elif path.name != 'installation.json' and str(path) not in pins:
                raise DockerError('production-installation-unlisted-file')
        for entry in sys.path:
            path = Path(entry)
            if not path.is_absolute():
                raise DockerError('production-python-path-unsafe')
            # CPython may include its nonexistent standard-library zip path.
            protected(path if path.exists() else path.parent, directory=not path.exists() or path.is_dir())
        for module in tuple(sys.modules.values()):
            path = getattr(module, '__file__', None)
            if path and not path.startswith('<'):
                protected(path)
        if not {'/usr/bin/docker', '/opt/portfolio-deployment/launcher.py',
                '/opt/portfolio-deployment/bindings.py'} <= pins.keys():
            raise DockerError('production-installation-tool-pins-incomplete')
        if str(Path(sys.executable).resolve()) not in pins:
            raise DockerError('production-installation-interpreter-pin-required')
        plugins = []
        for directory in ('/usr/local/lib/docker/cli-plugins', '/usr/local/libexec/docker/cli-plugins',
                          '/usr/lib/docker/cli-plugins', '/usr/libexec/docker/cli-plugins'):
            path = Path(directory)
            if path.exists():
                protected(path, directory=True)
                plugin = path / 'docker-compose'
                if plugin.exists():
                    protected(plugin)
                    plugins.append(str(plugin))
        if len(plugins) != 1 or plugins[0] not in pins:
            raise DockerError('production-installation-compose-pin-required')


class UnconfiguredInstallation:
    def verify(self):
        raise DockerError('production-trusted-installation-unavailable')
