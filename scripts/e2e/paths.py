"""Shared host paths; private state never depends on the source checkout location."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from scripts.deployment_journal import JournalError, private_directory
from scripts.e2e.safety import require

ROOT = Path(__file__).absolute().parents[2]


def resolve(kind, override=None):
    """Keep lexical components until the no-follow walk has rejected traversal."""
    variable, leaf = {"state": ("E2E_STATE_DIR", "e2e-state"),
                      "fixtures": ("E2E_FIXTURE_DIR", "e2e-fixtures")}[kind]
    value = override or os.environ.get(variable)
    try:
        path = Path(value).expanduser() if value else Path.home() / ".local/share/portfolio-deployment" / leaf
    except RuntimeError:
        require(False, "e2e-path-home-unavailable")
    require("\x00" not in str(path), "e2e-path-invalid")
    require(".." not in path.parts, "e2e-path-traversal-forbidden")
    return path.absolute()


@contextmanager
def directory(path):
    """Create missing parents using pinned descriptors; never repair existing modes."""
    path = Path(path)
    require(path.is_absolute() and path != Path("/") and ".." not in path.parts,
            "e2e-path-invalid")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    system_uid = os.fstat(fd).st_uid
    try:
        for index, part in enumerate(path.parts[1:]):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            leaf = index == len(path.parts) - 2
            require(info.st_uid in (system_uid, os.geteuid()) and
                    not (info.st_mode & 0o022 and not (info.st_uid == system_uid and info.st_mode & stat.S_ISVTX)) and
                    (not leaf or info.st_uid == os.geteuid() and not info.st_mode & 0o077),
                    "e2e-path-unsafe-permissions-or-owner")
        # Preserve the shared journal validator and its complete path checks.
        with private_directory(path) as validated:
            yield validated
    except JournalError as error:
        raise JournalError("e2e-path-" + str(error)) from None
    except OSError:
        raise JournalError("e2e-path-unavailable-or-symlink") from None
    finally:
        os.close(fd)


def check_legacy():
    """Unknown legacy contents are unresolved evidence, never permission to retry.

    Read directory entries only, even when the checkout ancestors are writable.
    Do not adopt, change permissions, migrate or delete legacy state.
    """
    for root in {ROOT, Path.cwd()}:
        for name in (".e2e-state", ".e2e-fixtures"):
            try:
                fd = os.open(root / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            except OSError:
                require(False, "e2e-legacy-state-review-required")
            try:
                with os.scandir(fd) as entries:
                    require(next(entries, None) is None, "e2e-legacy-state-review-required")
            except OSError:
                require(False, "e2e-legacy-state-review-required")
            finally:
                os.close(fd)
