"""Offline package/admin filesystem contracts; no privileged/live operations."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import zipfile

import pytest

from installation import operator_bootstrap as bootstrap
from installation.operator_bootstrap import (InitialInstallationApproval, InstallError, canonical,
                                            digest, read_bundle)
from scripts.production_integration import install_bundle

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('live socket attempted'))


def package(**overrides):
    files = {'launcher.py': b'# fixed launcher', 'bindings.py': b'raise RuntimeError()',
             'tools/caddy': b'not executed', 'scripts/production_integration/installation.py': b'',
             'schemas/test.json': b'{}', 'vendor/library.py': b'# pinned runtime',
             'manager/policy/admission.yaml': b'applications: []',
             'executor.service': (ROOT / 'installation/portfolio-deployment-executor.service').read_bytes()}
    files.update(overrides)
    manifest = {'version': 1, 'manager_sha': SHA,
                'files': [[n, digest(raw), 0o700 if n == 'tools/caddy' else 0o600] for n, raw in sorted(files.items())]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('bundle.json', canonical(manifest))
        for name, raw in files.items():
            archive.writestr(name, raw)
    return output.getvalue()


def rewrite(raw, name, data, **options):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w', **options) as dest:
        for item in source.infolist():
            dest.writestr(item.filename, data if item.filename == name else source.read(item.filename))
    return out.getvalue()


@pytest.mark.parametrize('name', ['../escape', '/escape', 'vendor/../escape', 'vendor//a',
                                  'vendor/link.pth', 'scripts/__pycache__/bad.pyc', 'scripts/.git/a',
                                  'vendor/pkg/tests/test.py', 'vendor/pkg/fixtures/a.json'])
def test_unsafe_bundle_names_rejected(name):
    with pytest.raises(InstallError):
        read_bundle(package(**{name: b'bad'}))


def test_bundle_integrity_duplicates_and_no_activation():
    raw = package()
    manifest, files = read_bundle(raw)
    assert manifest['manager_sha'] == SHA
    assert b'[Install]' not in files['executor.service'][0]
    assert b'Restart=no' in files['executor.service'][0]
    with pytest.raises(InstallError, match='content-changed'):
        read_bundle(rewrite(raw, 'launcher.py', b'changed'))
    bad = io.BytesIO(raw)
    with zipfile.ZipFile(bad, 'a') as archive:
        archive.writestr('extra.py', b'bad')
    with pytest.raises(InstallError):
        read_bundle(bad.getvalue())
    with pytest.raises(InstallError):
        read_bundle(rewrite(raw, 'bundle.json', b'{"version":1,"version":1}'))
    with pytest.raises(InstallError):
        read_bundle(package(**{'executor.service': b'[Install]\nWantedBy=multi-user.target'}))


@pytest.mark.parametrize('kind', ['symlink', 'compressed', 'collision', 'duplicate'])
def test_archive_shapes_rejected(kind):
    raw = package(**({'vendor': b'collision'} if kind == 'collision' else {}))
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, 'w') as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if kind == 'symlink' and item.filename == 'launcher.py':
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
            if kind == 'compressed':
                item.compress_type = zipfile.ZIP_DEFLATED
            dest.writestr(item, data)
        if kind == 'duplicate':
            with pytest.warns(UserWarning):
                dest.writestr('launcher.py', b'duplicate')
    with pytest.raises(InstallError):
        read_bundle(output.getvalue())


@pytest.fixture
def admin(tmp_path, monkeypatch):
    # Controlled filesystem only. Actual entrypoint refuses this writable checkout.
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path / 'opt/portfolio-deployment')
    monkeypatch.setattr(bootstrap, 'STORAGE', tmp_path / 'srv/portfolio')
    monkeypatch.setattr(bootstrap, 'UNIT', tmp_path / 'etc/systemd/system/portfolio-deployment-executor.service')
    monkeypatch.setattr(bootstrap, 'OWNER_UID', os.getuid())
    monkeypatch.setattr(bootstrap, 'admin_context', lambda: None)
    for p in (bootstrap.ROOT.parent, bootstrap.STORAGE.parent, bootstrap.UNIT.parent):
        p.mkdir(parents=True, mode=0o700)
    # /tmp is writable; retain all real metadata checks within the disposable root.
    metadata = bootstrap.metadata
    def checked(path, **kw):
        if path.is_relative_to(tmp_path):
            info = path.lstat()
            assert info.st_uid == os.getuid()
            leafdir = kw.get('directory', False)
            if not (stat.S_ISDIR(info.st_mode) if leafdir else stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                raise InstallError('installation-path-unsafe')
            if info.st_mode & 0o022:
                raise InstallError('installation-path-unsafe')
            if kw.get('private') and stat.S_IMODE(info.st_mode) != (0o700 if leafdir else 0o600):
                raise InstallError('installation-private-mode-invalid')
        else:
            metadata(path, **kw)
    monkeypatch.setattr(bootstrap, 'metadata', checked)
    monkeypatch.setattr(bootstrap.hashlib, 'file_digest', lambda stream, _: type('Pin', (), {'hexdigest': lambda s: 'b' * 64})())
    # System executables are not opened or modified by these fixtures.
    real_open = bootstrap.__builtins__['open']
    monkeypatch.setattr(bootstrap, 'open', lambda path, *a, **k: io.BytesIO(b'') if str(path).startswith('/usr/') else real_open(path, *a, **k), raising=False)
    monkeypatch.setattr(bootstrap, 'metadata', lambda p, **kw: None if str(p).startswith('/usr/') else checked(p, **kw))
    approval = InitialInstallationApproval(digest(package()), SHA, Path('/etc/machine-id').read_text().strip(),
        (('/usr/bin/docker', 'b'*64), (str(Path(os.sys.executable).resolve()), 'b'*64),
         ('/usr/lib/docker/cli-plugins/docker-compose', 'b'*64), ('/usr/bin/gh', 'b'*64),
         (str(bootstrap.HELPER / 'operator_bootstrap.py'), 'b'*64),
         (str(bootstrap.HELPER / 'admin_bindings.py'), 'b'*64)))
    return approval


def test_install_idempotent_preserves_data_lock_and_does_not_start(admin):
    raw = package()
    assert bootstrap.install(raw, admin)['executorStarted'] is False
    lock = bootstrap.STORAGE / 'locks/host.lock'
    inode = lock.stat().st_ino
    preserved = []
    for relative in ('journal/journal.sqlite3', 'certificates/cert', 'caddy-lkg/previous', 'state/recovery.json'):
        path = bootstrap.STORAGE / relative
        path.write_bytes(b'preserve')
        path.chmod(0o600)
        preserved.append(path)
    assert bootstrap.install(raw, admin)['status'] == 'installed'
    assert lock.stat().st_ino == inode
    assert all(p.read_bytes() == b'preserve' for p in preserved)
    assert not (bootstrap.STORAGE / 'caddy-config/Caddyfile').exists()
    assert not (bootstrap.STORAGE / 'requests/operation.json').exists()
    inventory = json.loads((bootstrap.ROOT / 'installation.json').read_bytes())
    assert inventory['manager_sha'] == SHA
    assert str(bootstrap.UNIT) in dict(inventory['files'])


@pytest.mark.parametrize('case', ['sha', 'artifact', 'foreign-storage', 'unit', 'symlink', 'unsafe-parent'])
def test_conflicts_fail_before_writes(admin, case):
    approval = admin
    if case == 'sha': approval = replace(admin, manager_sha='c'*40)
    if case == 'artifact': approval = replace(admin, bundle_sha256='c'*64)
    if case == 'foreign-storage': bootstrap.STORAGE.mkdir(mode=0o700)
    if case == 'unit': bootstrap.UNIT.write_bytes(b'foreign'); bootstrap.UNIT.chmod(0o600)
    if case == 'symlink': bootstrap.STORAGE.symlink_to(bootstrap.ROOT.parent)
    if case == 'unsafe-parent': bootstrap.ROOT.parent.chmod(0o777)
    with pytest.raises((InstallError, FileNotFoundError)):
        bootstrap.install(package(), approval)
    assert not bootstrap.ROOT.exists()
    assert not (bootstrap.STORAGE / 'locks/host.lock').exists()


def test_existing_installation_changes_and_missing_lock_are_not_repaired(admin):
    bootstrap.install(package(), admin)
    (bootstrap.ROOT / 'launcher.py').write_bytes(b'tampered')
    with pytest.raises(InstallError, match='conflict'):
        bootstrap.install(package(), admin)


@pytest.mark.parametrize('with_state', [False, True])
def test_missing_lock_with_state_is_not_recreated(admin, with_state):
    bootstrap.install(package(), admin)
    lock = bootstrap.STORAGE / 'locks/host.lock'
    lock.unlink()  # fixture models external damage, never installer behavior
    state = bootstrap.STORAGE / 'state/receipt'
    if with_state:
        state.write_bytes(b'preserve'); state.chmod(0o600)
    with pytest.raises(InstallError, match='canonical-lock-missing'):
        bootstrap.install(package(), admin)
    assert not lock.exists()
    assert not with_state or state.read_bytes() == b'preserve'


def test_operator_entry_refuses_checkout_and_unconfigured_binding():
    result = subprocess.run([os.sys.executable, '-I', '-S', '-B', str(ROOT / 'installation/operator_bootstrap.py')],
                            capture_output=True, timeout=5)
    assert result.returncode == 2 and b'blocked' in result.stdout
    from installation.admin_bindings import authenticate_initial_installation
    with pytest.raises(RuntimeError, match='unconfigured'):
        authenticate_initial_installation('a'*64)


def test_exact_git_export_is_deterministic_and_untrusted(tmp_path):
    sha = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    assets = {'tools/caddy': b'pinned tool', 'vendor/lib.py': b'pinned dependency'}
    pins = {name: digest(raw) for name, raw in assets.items()}
    raw = install_bundle.build_bundle(ROOT, sha, assets, pins)
    assert raw == install_bundle.build_bundle(ROOT, sha, assets, pins)
    manifest, files = read_bundle(raw)
    assert manifest['manager_sha'] == sha
    assert files['launcher.py'][0] == subprocess.check_output(['/usr/bin/git', 'show', sha+':installation/launcher.py'], cwd=ROOT)
    with pytest.raises(Exception):
        install_bundle.build_bundle(ROOT, '0'*40, assets, pins)
    with pytest.raises(InstallError):
        install_bundle.build_bundle(ROOT, sha, assets, {**pins, 'vendor/lib.py': '0'*64})


def test_make_bundle_entrypoint_and_missing_inputs(tmp_path):
    sha = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    vendor = tmp_path / 'vendor'; vendor.mkdir()
    (vendor / 'lib.py').write_bytes(b'# vendor')
    caddy = tmp_path / 'caddy'; caddy.write_bytes(b'pinned executable')
    pins = tmp_path / 'pins.json'
    pins.write_text(json.dumps({'vendor/lib.py': digest(b'# vendor'), 'tools/caddy': digest(b'pinned executable')}))
    output = tmp_path / 'bundle.zip'
    result = subprocess.run(['make', 'installation-bundle', 'PYTHON='+os.sys.executable,
        'MANAGER_REVISION='+sha, 'INSTALL_VENDOR='+str(vendor), 'INSTALL_CADDY='+str(caddy),
        'INSTALL_DEPENDENCY_PINS='+str(pins), 'INSTALL_BUNDLE='+str(output)], cwd=ROOT,
        capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['productionTrusted'] is False
    assert read_bundle(output.read_bytes())[0]['manager_sha'] == sha
    failed = subprocess.run(['make', 'installation-bundle', 'PYTHON='+os.sys.executable,
                              'INSTALL_VENDOR=', 'INSTALL_CADDY=', 'MANAGER_REVISION='],
                              cwd=ROOT, capture_output=True, timeout=5)
    assert failed.returncode != 0


@pytest.fixture
def bundle_review(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from scripts.production_integration import bundle_review as review
    from scripts.production_integration.release import MANAGER
    from scripts.production_integration.release_source import WorkflowCheck
    raw = package()
    _, files = read_bundle(raw)
    assets = tuple((n, digest(data)) for n, (data, _) in files.items() if n.startswith(('vendor/', 'tools/')))
    tree = []
    for n, (data, _) in files.items():
        if n.startswith(('vendor/', 'tools/')): continue
        source = {'launcher.py': 'installation/launcher.py', 'bindings.py': 'installation/bindings.py',
                  'executor.service': 'installation/portfolio-deployment-executor.service'}.get(n, n)
        if source.startswith('manager/'): source = source.removeprefix('manager/')
        tree.append({'path': source, 'mode': '100644', 'type': 'blob',
                     'sha': hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()})
    calls = []
    class Source:
        def __init__(self, github): pass
        def collect(self, repo, sha, checks):
            assert (repo, sha) == (MANAGER, SHA)
            calls.append('protected-main-with-app-checks')
    monkeypatch.setattr(review, 'ProtectedSource', Source)
    def api(repo, path):
        assert repo == MANAGER
        if path == '/git/commits/'+SHA: return {'sha': SHA, 'tree': {'sha': 'b'*40}}
        assert path == '/git/trees/'+'b'*40+'?recursive=1'
        return {'sha': 'b'*40, 'truncated': False, 'tree': tree}
    verifier = review.ManagerBundleVerifier(github=SimpleNamespace(get=api),
        checks=(WorkflowCheck(MANAGER, 'Offline validation', 1, '.github/workflows/ci.yml'),),
        asset_pins=assets)
    return SimpleNamespace(raw=raw, verifier=verifier, calls=calls, tree=tree, module=review)


def test_bundle_review_checks_exact_source_and_independent_dependency_pins(bundle_review):
    f = bundle_review
    proof = f.verifier.verify(f.raw)
    assert proof.manager_sha == SHA and proof.bundle_sha256 == digest(f.raw)
    assert f.calls == ['protected-main-with-app-checks'] * 2
    assert not hasattr(proof, 'productionAuthorized')


@pytest.mark.parametrize('case', ['source', 'dependency', 'truncated', 'no-main', 'no-policy', 'missing-source'])
def test_bundle_review_failclosed_without_authenticated_source(bundle_review, monkeypatch, case):
    from scripts.docker_adapter import DockerError
    f = bundle_review
    if case == 'source': f.tree[0]['sha'] = 'f'*40
    elif case == 'dependency': f.verifier.assets = tuple((n, 'f'*64) for n, p in f.verifier.assets)
    elif case == 'truncated':
        api = f.verifier.github.get
        f.verifier.github.get = lambda repo, path: {**api(repo, path), 'truncated': True} if '/trees/' in path else api(repo, path)
    elif case == 'no-main':
        def deny(*a, **k): raise DockerError('production-release-sha-not-on-main')
        monkeypatch.setattr(f.module.ProtectedSource, 'collect', deny)
    elif case == 'no-policy': f.verifier.checks = ()
    elif case == 'missing-source': f.tree.pop()
    with pytest.raises(DockerError): f.verifier.verify(f.raw)


def test_installer_never_executes_bundle_code(admin, tmp_path, monkeypatch):
    sentinel = tmp_path / 'executed'
    raw = package(**{'launcher.py': ('open('+repr(str(sentinel))+', "w").write("unsafe")').encode()})
    def forbidden(*args, **kwargs): pytest.fail('installer started a process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    approval = replace(admin, bundle_sha256=digest(raw))
    assert bootstrap.install(raw, approval)['executorStarted'] is False
    assert not sentinel.exists()
