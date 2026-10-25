"""Controlled kernel/SQLite sources; no Docker, Caddy, VM or host network access."""
from dataclasses import replace
import os
from pathlib import Path
import socket
import stat
import subprocess
from types import SimpleNamespace

import pytest

from scripts.deployment import host_lock, owns_host_lock
from scripts.deployment_journal import Journal
from scripts.docker_adapter import DockerError
from scripts.production_integration import local_sources, bootstrap_bindings
from scripts.production_integration import installation as sealed
from scripts.production_integration.gateway import content
from scripts.production_integration.local_sources import LocalHostSources, ReadOnlyJournalSource
from test_production_integration import paths
from scripts.production_integration.runtime import CaddyRuntime


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*a, **k): pytest.fail('live transport attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    store = tmp_path / 'state'
    store.mkdir(mode=0o700)
    monkeypatch.setattr(local_sources, 'PATHS', (str(store),))
    monkeypatch.setattr(local_sources, 'validate_storage_directory', lambda *a, **k: None)
    calls = []
    def run(argv, **kwargs):
        assert tuple(argv) in local_sources.COMMANDS.values()
        assert 0 < kwargs['timeout'] <= 2
        calls.append(tuple(argv))
        return b'tcp LISTEN 0 128 127.0.0.1:2222 0.0.0.0:*\n' if argv[0] == '/usr/bin/ss' else b'[]'
    monkeypatch.setattr(local_sources, 'run_bounded', run)
    installation = SimpleNamespace(verify=lambda: None, files=tuple((cmd[0], 'a'*64) for cmd in local_sources.COMMANDS.values()))
    engine = SimpleNamespace(identity=lambda: ('a'*32, 'daemon'))
    reader = LocalHostSources(installation, engine, clock=lambda: 0)
    return SimpleNamespace(reader=reader, engine=engine, installation=installation, calls=calls)


def test_bounded_host_reads_emit_facts_without_source_authority(sources):
    facts = sources.reader.collect()
    assert facts.identity == ('a'*32, 'daemon')
    assert facts.network['listeners'][0]['port'] == 2222
    assert facts.storage[0]['availableBytes'] > 0
    assert not hasattr(facts, 'verified')
    assert not hasattr(facts, 'bootstrap_verified')
    assert len(sources.calls) == 5


def test_host_reads_use_exact_pinned_ip_path(sources, monkeypatch):
    # Literal expectations avoid deriving the regression oracle from COMMANDS.
    sources.installation.files = (('/usr/bin/ip', 'a'*64), ('/usr/bin/ss', 'b'*64))
    monkeypatch.setenv('PATH', '/untrusted')
    sources.reader.collect()
    assert sources.calls == [
        ('/usr/bin/ip', '-j', '-4', 'route', 'show', 'table', 'all'),
        ('/usr/bin/ip', '-j', '-6', 'route', 'show', 'table', 'all'),
        ('/usr/bin/ip', '-j', '-4', 'rule', 'show'),
        ('/usr/bin/ip', '-j', '-6', 'rule', 'show'),
        ('/usr/bin/ss', '-H', '-lntu'),
    ]


@pytest.mark.parametrize('ip_pin', ['/usr/sbin/ip', None])
def test_old_path_or_missing_ip_pin_blocks_before_commands(sources, ip_pin):
    sources.installation.files = (('/usr/bin/ss', 'a'*64),)
    if ip_pin is not None:
        sources.installation.files += ((ip_pin, 'b'*64),)
    with pytest.raises(DockerError, match='^production-host-source-tool-unpinned$'):
        sources.reader.collect()
    assert not sources.calls


def test_missing_listener_pin_blocks_listener_execution(sources):
    sources.installation.files = (('/usr/bin/ip', 'a'*64),)
    with pytest.raises(DockerError, match='^production-host-source-tool-unpinned$'):
        sources.reader.collect()
    assert len(sources.calls) == 4
    assert all(argv[0] == '/usr/bin/ip' for argv in sources.calls)


@pytest.mark.parametrize('path,change', [
    ('/usr/bin/ip', {'st_mode': stat.S_IFLNK | 0o777}),
    ('/usr/bin', {'st_mode': stat.S_IFLNK | 0o777}),
    ('/usr/bin/ip', {'st_nlink': 2}),
    ('/usr/bin/ip', {'st_uid': 1000}),
    ('/usr/bin/ip', {'st_mode': stat.S_IFREG | 0o775}),
])
def test_unsafe_pinned_ip_is_rejected_by_sealed_verification(sources, monkeypatch, path, change):
    def metadata(candidate):
        values = dict(st_uid=0, st_nlink=1, st_mode=(stat.S_IFREG | 0o755)
                      if str(candidate) == '/usr/bin/ip' else (stat.S_IFDIR | 0o755))
        if str(candidate) == path:
            values.update(change)
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path, 'lstat', metadata)
    monkeypatch.setattr(sealed.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(sealed, 'sys', SimpleNamespace(
        flags=SimpleNamespace(isolated=True, ignore_environment=True, no_user_site=True, no_site=True),
        dont_write_bytecode=True))
    sources.reader.installation = sealed.SealedInstallation('a'*40, (('/usr/bin/ip', 'b'*64),))
    with pytest.raises(DockerError, match='^production-installation-metadata-unsafe$'):
        sources.reader.collect()
    assert not sources.calls


@pytest.mark.parametrize('case', ['unpinned', 'malformed-routes', 'malformed-listeners', 'oversized', 'identity', 'expired', 'permission'])
def test_unknown_sources_never_become_empty_or_trusted(sources, monkeypatch, case):
    if case == 'unpinned': sources.installation.files = ()
    elif case == 'malformed-routes': monkeypatch.setattr(local_sources, 'run_bounded', lambda *a, **k: b'{}')
    elif case == 'malformed-listeners': monkeypatch.setattr(local_sources, 'run_bounded', lambda a, **k: b'[]' if a[0] != '/usr/bin/ss' else b'tcp bad')
    elif case == 'oversized': monkeypatch.setattr(local_sources, 'run_bounded', lambda *a, **k: b'[{}'+b',{}'*512+b']')
    elif case == 'identity':
        identities = iter([('a'*32, 'daemon'), ('b'*32, 'foreign')])
        sources.engine.identity = lambda: next(identities)
    elif case == 'expired':
        stamps = iter([0, 6])
        sources.reader.clock = lambda: next(stamps)
    else:
        def denied(*a, **k): raise DockerError('docker-permission-denied')
        monkeypatch.setattr(local_sources, 'run_bounded', denied)
    with pytest.raises(DockerError): sources.reader.collect()


@pytest.fixture
def journal_source(tmp_path, monkeypatch):
    directory = tmp_path / 'journal'
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(local_sources, 'JOURNAL_DIRECTORY', str(directory))
    monkeypatch.setattr(local_sources, 'validate_storage_directory', lambda *a, **k: None)
    monkeypatch.setattr(local_sources, 'protected', lambda *a, **k: None)
    def lease_check(lease):
        if not owns_host_lock(lease): raise DockerError('lease required')
    monkeypatch.setattr(local_sources, 'require_production_lease', lease_check)
    return directory, tmp_path / 'locks'


def test_journal_read_is_readonly_and_absence_does_not_create(journal_source):
    directory, locks = journal_source
    reader = ReadOnlyJournalSource()
    with host_lock(locks) as lease:
        assert reader.read(lease) == {'database': 'absent', 'recovery': 'unestablished'}
        assert not list(directory.iterdir())
        with Journal(directory, lease): pass
        path = directory / 'journal.sqlite3'
        before, inode = path.read_bytes(), path.stat().st_ino
        facts = reader.read(lease)
        assert facts['database'] == 'present' and not facts['authorizations']
        assert path.read_bytes() == before and path.stat().st_ino == inode
    with pytest.raises(DockerError): reader.read(lease)


@pytest.mark.parametrize('case', ['sidecar', 'foreign', 'mode'])
def test_journal_source_rejects_recovery_or_unsafe_storage(journal_source, case):
    directory, locks = journal_source
    with host_lock(locks) as lease:
        with Journal(directory, lease): pass
        path = directory / 'journal.sqlite3'
        if case == 'sidecar': (directory / 'journal.sqlite3-journal').write_bytes(b'unknown interrupted write')
        elif case == 'foreign': path.write_bytes(b'foreign')
        else: path.chmod(0o644)
        before = path.read_bytes()
        with pytest.raises(Exception): ReadOnlyJournalSource().read(lease)
        assert path.read_bytes() == before


def test_machine_identity_requires_exact_protected_file(monkeypatch):
    calls = []
    monkeypatch.setattr(local_sources, 'protected', lambda p: calls.append(p))
    actual = local_sources.MachineIdentity()()
    assert len(actual) == 32 and calls == [Path('/etc/machine-id')]
    def deny(*a): raise DockerError('unsafe')
    monkeypatch.setattr(local_sources, 'protected', deny)
    with pytest.raises(DockerError): local_sources.MachineIdentity()()


def test_empty_caddy_validation_requires_independent_resources_and_pin(monkeypatch):
    runtime = CaddyRuntime('caddy@sha256:' + 'a'*64, '2.11.0', paths())
    installation = SimpleNamespace(verify=lambda:None, files=((bootstrap_bindings.BINARY, 'a'*64),))
    calls = []
    monkeypatch.setattr(bootstrap_bindings, 'protected', lambda *a: None)
    monkeypatch.setattr(bootstrap_bindings, 'run_bounded', lambda argv, **kwargs: calls.append(argv))
    binding = bootstrap_bindings.BootstrapValidationBinding(installation, runtime)
    with pytest.raises(DockerError): binding.preflight('prepared', runtime)
    assert not calls
    binding.resources = SimpleNamespace(preflight=lambda p, r:p, verify_created=lambda p, r:'external-proof')
    assert binding.preflight('prepared', runtime) == 'prepared'
    assert [c[1] for c in calls] == ['adapt', 'validate']
    assert binding.verify_created('prepared', runtime) == 'external-proof'
    with pytest.raises(DockerError): binding.validate_empty(runtime, 'arbitrary route')
    installation.files = ()
    with pytest.raises(DockerError): binding.validate_empty(runtime, content(()))


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
@pytest.mark.parametrize('symlink', [False, True])
def test_absent_database_with_sidecar_never_qualifies_for_initial_update(journal_source, suffix, symlink):
    directory, locks = journal_source
    sidecar = directory / ('journal.sqlite3' + suffix)
    if symlink:
        sidecar.symlink_to(directory / 'missing')
    else:
        sidecar.write_bytes(b'recovery evidence')
    inode = sidecar.lstat().st_ino
    with host_lock(locks) as lease:
        with pytest.raises(DockerError, match='recovery-sidecar-present'):
            ReadOnlyJournalSource().read(lease)
    assert not (directory / 'journal.sqlite3').exists()
    assert sidecar.lstat().st_ino == inode
