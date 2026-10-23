#!/usr/bin/python3
"""Standalone stdlib-only operator bootstrap. Independently install/pin before root use.

No approval deserializer, shell, subprocess, Docker, systemctl or bundle imports.
The independent administrative channel owns authentication and exact artifact pins.
"""
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

ROOT = Path('/opt/portfolio-deployment')
STORAGE = Path('/srv/portfolio')
UNIT = Path('/etc/systemd/system/portfolio-deployment-executor.service')
HELPER = Path('/usr/local/lib/portfolio-deployment-bootstrap')
INPUT = Path('/var/lib/portfolio-deployment-bootstrap/bundle.zip')
OWNER_UID = 0
LIMIT = 128 * 1024 * 1024
FILE_LIMIT = 64 * 1024 * 1024
DIRECTORIES = ('locks', 'state', 'journal', 'requests', 'caddy-config', 'caddy-lkg',
               'certificates', 'caddy-data', 'temporary')
OWNER = b'DanilFaritovich/portfolio-deployment:storage-v1\n'


class InstallError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise InstallError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate-json-key')
        result[key] = value
    return result


def json_bytes(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(InstallError('nonfinite-json')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def safe_name(name):
    require(isinstance(name, str) and 0 < len(name) <= 512 and
            re.fullmatch(r'[A-Za-z0-9_./-]+', name) and
            str(PurePosixPath(name)) == name and not name.startswith('/') and
            not {'.', '..', '.git', '__pycache__', 'tests', 'fixtures'} & set(name.split('/')) and
            not name.endswith(('.pyc', '.pth')),
            'bundle-path-invalid')
    return name


def allowed(name):
    return (name in ('launcher.py', 'bindings.py', 'tools/caddy') or
            name.startswith(('scripts/', 'schemas/', 'vendor/', 'manager/registry/applications/')) or
            name == 'manager/policy/admission.yaml')


def read_bundle(raw):
    """No extraction. Reject compression, links, duplicate/extra files and bombs."""
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT, 'bundle-size-invalid')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(0 < len(entries) <= 4096 and len({e.filename for e in entries}) == len(entries),
                    'bundle-inventory-invalid')
            require(sum(e.file_size for e in entries) <= LIMIT, 'bundle-size-invalid')
            for e in entries:
                safe_name(e.filename)
                require(e.compress_type == zipfile.ZIP_STORED and not e.flag_bits & 1 and
                        0 <= e.file_size <= FILE_LIMIT and
                        stat.S_IFMT(e.external_attr >> 16) in (0, stat.S_IFREG), 'bundle-entry-invalid')
            manifest = json_bytes(archive.read('bundle.json'))
            require(set(manifest) == {'version', 'manager_sha', 'files'} and type(manifest['version']) is int and manifest['version'] == 1 and
                    re.fullmatch(r'[0-9a-f]{40}', manifest['manager_sha']) and
                    isinstance(manifest['files'], list), 'bundle-manifest-invalid')
            files = {}
            for item in manifest['files']:
                require(isinstance(item, list) and len(item) == 3, 'bundle-inventory-invalid')
                name, pin, mode = item
                safe_name(name)
                require((allowed(name) or name == 'executor.service') and name not in files and
                        isinstance(pin, str) and re.fullmatch(r'[0-9a-f]{64}', pin) and
                        type(mode) is int and mode in (0o600, 0o700), 'bundle-inventory-invalid')
                data = archive.read(name)
                require(digest(data) == pin, 'bundle-content-changed')
                files[name] = (data, mode)
            require({e.filename for e in entries} == {'bundle.json', *files} and
                    {'launcher.py', 'bindings.py', 'tools/caddy', 'executor.service',
                     'scripts/production_integration/installation.py', 'manager/policy/admission.yaml'} <= files.keys() and
                    any(n.startswith('vendor/') for n in files) and
                    any(n.startswith('schemas/') for n in files), 'bundle-inventory-incomplete')
            # File/ancestor collisions are rejected before any filesystem write.
            require(not any(str(p) in files for n in files for p in PurePosixPath(n).parents),
                    'bundle-path-collision')
            validate_unit(files['executor.service'][0])
            return manifest, files
    except (KeyError, ValueError, TypeError, OSError, zipfile.BadZipFile, RecursionError):
        raise InstallError('bundle-invalid') from None


def validate_unit(raw):
    expected = [
        '[Unit]',
        'Description=Portfolio deployment one-shot executor (root-equivalent Docker authority)',
        'After=docker.service', 'Requires=docker.service', '[Service]', 'Type=oneshot', 'User=root', 'Group=root',
        'UMask=0077', 'WorkingDirectory=/opt/portfolio-deployment',
        'ExecStart=/usr/bin/python3 -I -S -B /opt/portfolio-deployment/launcher.py',
        'Environment=LANG=C',
        'UnsetEnvironment=PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG COMPOSE_FILE COMPOSE_PROJECT_NAME',
        'StandardInput=null', 'TimeoutStartSec=30min', 'KillMode=control-group', 'Restart=no',
    ]
    lines = [n.strip() for n in raw.decode('ascii').splitlines() if n.strip() and not n.lstrip().startswith('#')]
    require(lines == expected,
            'one-shot-unit-contract-invalid')


@dataclass(frozen=True)
class InitialInstallationApproval:
    """Only independent installed admin channel may produce this trusted object.

    This is not an operator approval signature format or executor authorization.
    External pins include resolved interpreter/stdlib, Docker/Compose, gh and roots.
    """
    bundle_sha256: str
    manager_sha: str
    host_id: str
    external_files: tuple[tuple[str, str], ...]


def metadata(path, *, directory=False, private=False):
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        is_dir = parent != path or directory
        require(info.st_uid == OWNER_UID and not info.st_mode & 0o022 and
                (stat.S_ISDIR(info.st_mode) if is_dir else
                 stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'installation-path-unsafe')
    require(not private or stat.S_IMODE(path.lstat().st_mode) == (0o700 if directory else 0o600),
            'installation-private-mode-invalid')


def matches(path, expected):
    if path.stat().st_size != len(expected):
        return False
    with path.open('rb') as stream:
        return stream.read(len(expected) + 1) == expected


def private_mkdir(path):
    path.mkdir(mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o700)  # new directory only; independent of caller umask
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)


def exclusive(path, raw, mode=0o600):
    """Retain partial writes on error; never unlink/retry an ambiguous outcome."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def admin_context():
    require(os.geteuid() == 0 and sys.flags.isolated and sys.flags.no_site and
            sys.dont_write_bytecode and Path(__file__) == HELPER / 'operator_bootstrap.py',
            'independently-installed-isolated-bootstrap-required')
    metadata(HELPER, directory=True)
    metadata(HELPER / 'operator_bootstrap.py')


def install(raw, approval):
    """Initial installation only. No updates/adoption or executable bundle code.

    Call under a quiescent authenticated administrative installation window.
    Canonical host flock serializes subsequent idempotent checks and all writers.
    """
    import fcntl
    admin_context()
    require(type(approval) is InitialInstallationApproval and
            digest(raw) == approval.bundle_sha256, 'independent-bundle-approval-required')
    manifest, files = read_bundle(raw)
    require(manifest['manager_sha'] == approval.manager_sha and
            re.fullmatch(r'[0-9a-f]{32}', approval.host_id) and
            Path('/etc/machine-id').read_text().strip() == approval.host_id, 'approved-source-or-host-mismatch')
    external = dict(approval.external_files)
    require(len(external) == len(approval.external_files) and
            {'/usr/bin/docker', str(Path(sys.executable).resolve()),
             str(HELPER / 'operator_bootstrap.py'), str(HELPER / 'admin_bindings.py')} <= external.keys() and
            any(p.endswith('/docker-compose') for p in external) and
            any(p.endswith('/gh') for p in external), 'external-tool-inventory-incomplete')
    for path, pin in external.items():
        require(isinstance(path, str) and path.startswith('/') and str(Path(path)) == path and
                '..' not in Path(path).parts and not Path(path).is_relative_to(ROOT) and
                path != str(UNIT) and re.fullmatch(r'[0-9a-f]{64}', pin), 'external-pin-invalid')
        metadata(Path(path))
        with open(path, 'rb') as stream:
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == pin, 'external-content-changed')
    # Preflight every destination, including foreign state, BEFORE creating paths.
    metadata(ROOT.parent, directory=True)
    metadata(STORAGE.parent, directory=True)
    metadata(UNIT.parent, directory=True)
    inventory = {str(ROOT / n): digest(data) for n, (data, _) in files.items() if n != 'executor.service'}
    inventory.update(external)
    inventory[str(UNIT)] = digest(files['executor.service'][0])
    installed_inventory = canonical({'manager_sha': approval.manager_sha, 'files': sorted(map(list, inventory.items()))})
    if ROOT.exists() or ROOT.is_symlink():
        metadata(ROOT, directory=True, private=True)
        expected = {ROOT / n for n in files if n != 'executor.service'} | {ROOT / 'installation.json'}
        actual = set()
        for count, p in enumerate(ROOT.rglob('*'), 1):
            require(count <= 8192, 'existing-installation-inventory-limit')
            metadata(p, directory=p.is_dir(), private=p.is_dir())
            if not p.is_dir():
                actual.add(p)
        require(actual == expected, 'existing-installation-conflict')
        require(matches(ROOT / 'installation.json', installed_inventory),
                'existing-installation-conflict')
        for n, (data, mode) in files.items():
            if n != 'executor.service':
                require(matches(ROOT / n, data) and
                        stat.S_IMODE((ROOT / n).stat().st_mode) == mode, 'existing-installation-conflict')
    if UNIT.exists() or UNIT.is_symlink():
        metadata(UNIT, private=True)
        require(matches(UNIT, files['executor.service'][0]), 'existing-unit-conflict')
    storage_exists = STORAGE.exists() or STORAGE.is_symlink()
    if ROOT.exists() and not storage_exists:
        raise InstallError('existing-installation-storage-missing')
    if storage_exists:
        metadata(STORAGE, directory=True, private=True)
        marker = STORAGE / '.manager-storage-v1'
        metadata(marker, private=True)
        require(matches(marker, OWNER), 'storage-ownership-unconfirmed')
    for name in DIRECTORIES:
        p = STORAGE / name
        if storage_exists and not (p.exists() or p.is_symlink()):
            raise InstallError('existing-storage-incomplete')
        if p.exists() or p.is_symlink():
            metadata(p, directory=True, private=True)
            # Ownership marker does not excuse unsafe nested resources.
            count = 0
            for child in p.rglob('*'):
                count += 1
                require(count <= 4096, 'storage-inventory-limit')
                metadata(child, directory=child.is_dir())
                require(not child.lstat().st_mode & 0o077, 'storage-content-mode-invalid')
    lock = STORAGE / 'locks/host.lock'
    if lock.exists() or lock.is_symlink():
        metadata(lock, private=True)
    if storage_exists and not lock.exists():
        raise InstallError('canonical-lock-missing-with-existing-state')
    if not lock.exists():
        require(not any(p.exists() and any(p.iterdir()) for p in (STORAGE / n for n in DIRECTORIES)),
                'canonical-lock-missing-with-existing-state')
    # Do not initialize SQLite, config, certificates or LKG during provisioning.
    if not storage_exists:
        private_mkdir(STORAGE)
        exclusive(STORAGE / '.manager-storage-v1', OWNER)
        sync_directory(STORAGE.parent)
    for name in DIRECTORIES:
        p = STORAGE / name
        if not p.exists():
            private_mkdir(p)
            sync_directory(STORAGE)
    if not lock.exists():
        # A new inode is allowed only for genuinely initial, empty manager state.
        require(not any(any((STORAGE / n).iterdir()) for n in DIRECTORIES),
                'canonical-lock-missing-with-existing-state')
        exclusive(lock, b'')
    with lock.open('rb') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require((os.fstat(lease.fileno()).st_dev, os.fstat(lease.fileno()).st_ino) ==
                (lock.stat().st_dev, lock.stat().st_ino), 'canonical-lock-changed')
        if not ROOT.exists():
            private_mkdir(ROOT)
            for n, (data, mode) in sorted(files.items()):
                if n == 'executor.service':
                    continue
                target = ROOT / n
                for parent in reversed(target.relative_to(ROOT).parents):
                    p = ROOT / parent
                    if not p.exists():
                        private_mkdir(p)
                        sync_directory(p.parent)
                exclusive(target, data, mode)
            exclusive(ROOT / 'installation.json', installed_inventory)
            sync_directory(ROOT.parent)
        if not UNIT.exists():
            exclusive(UNIT, files['executor.service'][0])
    return {'status': 'installed', 'manager_sha': approval.manager_sha, 'executorStarted': False}


def main():
    try:
        require(len(sys.argv) == 1, 'bootstrap-arguments-forbidden')
        admin_context()  # reject writable checkout BEFORE importing a binding
        metadata(HELPER / 'admin_bindings.py')
        metadata(INPUT, private=True)
        with INPUT.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        require(len(raw) <= LIMIT, 'bundle-size-invalid')
        sys.path.insert(0, str(HELPER))
        sys.modules['operator_bootstrap'] = sys.modules[__name__]
        import admin_bindings
        # No bundled Python or request-selected module is imported here.
        approval = admin_bindings.authenticate_initial_installation(digest(raw))
        print(json.dumps(install(raw, approval)))
        return 0
    except Exception:
        print('{"status":"blocked","executorStarted":false,"reason":"independent-installation-review-required"}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
