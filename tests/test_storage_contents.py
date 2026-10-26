"""Offline regression coverage for protected storage read stability.

Access-time updates are normal reads, not evidence of mutation. All security-
relevant metadata changes must still reject an in-flight content observation.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.docker_adapter import DockerError
from scripts.production_integration import host_policy as host


FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
          'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / 'protected'
    root.mkdir(mode=0o700)
    path = root / 'Caddyfile'
    path.write_bytes(b'fixed accepted configuration')
    path.chmod(0o600)
    monkeypatch.setattr(host, 'PATHS', (str(root),))
    # These fixtures are not root-owned; ownership protection is tested by
    # installation tests. Here the focus is the post-read identity check.
    monkeypatch.setattr(host, 'protected', lambda *args, **kwargs: None)
    return path


def changed(info, field):
    values = {key: getattr(info, key) for key in FIELDS}
    values[field] += 1
    return SimpleNamespace(**values)


@pytest.mark.parametrize('where', ['descriptor', 'path'])
@pytest.mark.parametrize('field', [
    'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns', 'st_size', 'st_mode',
    'st_nlink', 'st_uid', 'st_gid', 'st_ino', 'st_dev',
])
def test_storage_read_ignores_atime_but_rejects_changed_identity(storage, monkeypatch, where, field):
    if where == 'descriptor':
        original = host.os.fstat
        count = 0

        def fstat(fd):
            nonlocal count
            info = original(fd)
            count += 1
            return changed(info, field) if count == 2 else info

        monkeypatch.setattr(host.os, 'fstat', fstat)
    else:
        original = Path.lstat
        count = 0

        def lstat(path):
            nonlocal count
            info = original(path)
            if path == storage:
                count += 1
                if count == 2:
                    return changed(info, field)
            return info

        monkeypatch.setattr(Path, 'lstat', lstat)

    if field == 'st_atime_ns':
        records = host.storage_contents()
        file_record = next(item for item in records if item['path'] == str(storage))
        assert len(file_record['digest']) == 64
    else:
        with pytest.raises(DockerError, match='production-storage-content-changed'):
            host.storage_contents()
