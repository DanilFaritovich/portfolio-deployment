"""One read-only observation window between complete sealed verifications.

Only unchanged file identities may reuse the opening hash verification. Changes
fail closed; they never refresh a cached digest. No authority survives the scope.
"""
from contextlib import contextmanager
from pathlib import Path
import stat
import sys

from scripts.production_integration.observations import require


def identity(info):
    if stat.S_ISDIR(info.st_mode):
        # Unrelated child creation changes directory times, not path protection.
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class VerifiedRead:
    def __init__(self, installation):
        self.installation, self.active, self.identities = installation, False, None

    def paths(self):
        paths = {Path(name) for name, _ in self.installation.files}
        paths.update(Path(module.__file__) for module in tuple(sys.modules.values())
                     if getattr(module, '__file__', None) and not module.__file__.startswith('<'))
        paths.update(Path(entry) if Path(entry).exists() else Path(entry).parent for entry in sys.path)
        require(all(path.is_absolute() for path in paths), 'production-read-window-path-unsafe')
        return paths | {parent for path in paths for parent in path.parents}

    def capture(self):
        return {str(path): identity(path.lstat()) for path in self.paths()}

    def verify(self):
        require(self.active and self.identities is not None, 'production-read-window-inactive')
        require(self.capture() == self.identities, 'production-read-window-files-changed')

    @contextmanager
    def open(self):
        require(not self.active and self.identities is None, 'production-read-window-already-used')
        self.identities = self.capture()
        self.installation.verify()
        require(self.capture() == self.identities, 'production-read-window-files-changed')
        self.active = True
        try:
            yield self
            self.verify()
            self.installation.verify()
            self.verify()
        finally:
            self.active = False
