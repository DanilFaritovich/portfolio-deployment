"""Scoped reuse of sealed hashes requires unchanged identities and final rehash."""
from types import SimpleNamespace
import os

import pytest

from scripts.docker_adapter import DockerError
from scripts.production_integration.verified_read import VerifiedRead


@pytest.fixture
def window(tmp_path, monkeypatch):
    source = tmp_path / 'source.py'
    source.write_bytes(b'original')
    calls = []
    installation = SimpleNamespace(files=((str(source), 'fixture'),),
                                   verify=lambda: calls.append('full-hash-verification'))
    window = VerifiedRead(installation)
    monkeypatch.setattr(window, 'paths', lambda: {source, tmp_path})
    return window, source, calls


def test_full_verification_brackets_window_without_rehash_on_every_read(window):
    scope, source, calls = window
    with pytest.raises(DockerError, match='inactive'): scope.verify()
    with scope.open():
        for _ in range(4): scope.verify()
        assert calls == ['full-hash-verification']
        (source.parent / 'unrelated').write_bytes(b'unrelated')
        scope.verify()
    assert calls == ['full-hash-verification'] * 2
    with pytest.raises(DockerError, match='inactive'): scope.verify()
    with pytest.raises(DockerError, match='already-used'):
        with scope.open(): pass


@pytest.mark.parametrize('change', ['contents', 'mode', 'replacement', 'symlink', 'removed'])
def test_any_file_change_invalidates_window(window, change):
    scope, source, _ = window
    with pytest.raises((DockerError, OSError)):
        with scope.open():
            if change == 'contents':
                before = source.stat()
                source.write_bytes(b'modified')
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            elif change == 'mode': source.chmod(0o700)
            elif change == 'replacement':
                replacement = source.with_suffix('.new')
                replacement.write_bytes(b'original')
                replacement.replace(source)
            elif change == 'symlink':
                target = source.with_suffix('.target')
                source.rename(target)
                source.symlink_to(target)
            else: source.unlink()
            scope.verify()
    assert not scope.active


@pytest.mark.parametrize('when', ['opening', 'closing'])
def test_change_during_full_verification_cannot_be_adopted(window, when):
    scope, source, calls = window
    def verify():
        calls.append(1)
        if len(calls) == (1 if when == 'opening' else 2): source.write_bytes(b'changed')
    scope.installation.verify = verify
    with pytest.raises(DockerError, match='files-changed'):
        with scope.open(): pass
    assert not scope.active


def test_exception_invalidates_scope_without_returning_authority(window):
    scope, _, _ = window
    with pytest.raises(RuntimeError):
        with scope.open(): raise RuntimeError('source failed')
    with pytest.raises(DockerError, match='inactive'): scope.verify()
