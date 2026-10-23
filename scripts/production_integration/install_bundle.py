"""Deterministic offline export of exact Git objects plus hash-locked runtime assets.

Build output is UNTRUSTED until independent manager-source/dependency and bundle
verification through the administrative bootstrap channel. No develop promotion.
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from installation.operator_bootstrap import (FILE_LIMIT, InstallError, canonical, digest,
                                            exclusive, read_bundle, require, safe_name)
from scripts.docker_adapter import run_bounded


RUNTIME_ROOT_MODULES = frozenset({
    'acceptance_contract', 'application_acceptance', 'compose', 'compose_version',
    'composite_state', 'deployment', 'deployment_journal', 'deployment_lifecycle',
    'docker_adapter', 'docker_discovery', 'gateway', 'planning_inputs', 'readiness',
    'reconciliation', 'runtime_contract', 'runtime_security', 'validate_registry',
})


def source_target(name):
    """Reviewed runtime source selection, shared with independent source review."""
    if name.startswith('scripts/'):
        if name.startswith('scripts/production_integration/'):
            return None if name.rsplit('/', 1)[-1] in ('install_bundle.py', 'bundle_review.py', 'cli.py') else name
        return name if Path(name).parent.as_posix() == 'scripts' and Path(name).stem in RUNTIME_ROOT_MODULES else None
    if name.startswith('installation/'):
        return {'installation/launcher.py': 'launcher.py', 'installation/bindings.py': 'bindings.py',
                'installation/portfolio-deployment-executor.service': 'executor.service'}.get(name)
    if name.startswith(('registry/applications/', 'policy/')):
        return 'manager/' + name if name.startswith('registry/') or name == 'policy/admission.yaml' else None
    return name if name.startswith('schemas/') else None


def build_bundle(repository, revision, assets, dependency_pins, *, bindings=None):
    require(os.geteuid() != 0, 'bundle-build-must-be-unprivileged')
    require(isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision), 'exact-manager-sha-required')
    repository = Path(repository).resolve()
    def git(*args):
        return run_bounded(['/usr/bin/git', '--no-replace-objects', '-C', str(repository), *args], cwd=str(repository))
    require(git('rev-parse', '--verify', revision + '^{commit}').decode().strip() == revision,
            'manager-sha-unavailable')
    listing = git('ls-tree', '-rz', revision, '--', 'scripts', 'schemas', 'registry/applications',
                  'policy/admission.yaml', 'installation')
    files = {}
    for record in listing.split(b'\0'):
        if not record:
            continue
        head, name = record.split(b'\t')
        mode, kind, oid = head.decode().split()
        name = name.decode('ascii')
        safe_name(name)
        require(mode in ('100644', '100755') and kind == 'blob', 'source-links-or-submodules-forbidden')
        target = source_target(name)
        if target is None:
            continue
        files[target] = (git('cat-file', 'blob', oid), 0o600)
    require(set(assets) == set(dependency_pins) and 'tools/caddy' in assets and
            any(n.startswith('vendor/') for n in assets), 'runtime-dependency-inventory-incomplete')
    for name, raw in assets.items():
        safe_name(name)
        require(name == 'tools/caddy' or name.startswith('vendor/'), 'runtime-asset-path-invalid')
        require(type(raw) is bytes and len(raw) <= FILE_LIMIT and digest(raw) == dependency_pins[name],
                'runtime-dependency-hash-mismatch')
        files[name] = (raw, 0o700 if name == 'tools/caddy' else 0o600)
    if bindings is not None:
        require(type(bindings) is bytes and 0 < len(bindings) <= FILE_LIMIT, 'bindings-invalid')
        files['bindings.py'] = (bindings, 0o600)
    manifest = {'version': 1, 'manager_sha': revision,
                'files': [[n, digest(raw), mode] for n, (raw, mode) in sorted(files.items())]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in [('bundle.json', canonical(manifest)), *[(n, r) for n, (r, _) in sorted(files.items())]]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, raw)
    raw = output.getvalue()
    read_bundle(raw)
    return raw


def path_argument(value):
    if not value.strip():
        raise argparse.ArgumentTypeError('explicit nonempty path required')
    return Path(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--vendor', required=True, type=path_argument)
    parser.add_argument('--caddy', required=True, type=path_argument)
    parser.add_argument('--dependency-pins', required=True, type=path_argument)
    parser.add_argument('--bindings', type=path_argument)
    parser.add_argument('--output', required=True, type=path_argument)
    args = parser.parse_args(argv)
    try:
        assets = {'tools/caddy': args.caddy.read_bytes()}
        require(args.vendor.is_dir() and not args.vendor.is_symlink(), 'vendor-directory-invalid')
        for path in sorted(args.vendor.rglob('*')):
            require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'runtime-asset-type-invalid')
            if path.is_file():
                require(path.stat().st_size <= FILE_LIMIT, 'runtime-asset-size-invalid')
                assets['vendor/' + path.relative_to(args.vendor).as_posix()] = path.read_bytes()
        from installation.operator_bootstrap import json_bytes
        pins = json_bytes(args.dependency_pins.read_bytes())
        raw = build_bundle(Path(__file__).resolve().parents[2], args.revision, assets, pins,
                           bindings=args.bindings.read_bytes() if args.bindings else None)
        exclusive(args.output, raw)
        print(json.dumps({'bundle_sha256': digest(raw), 'manager_sha': args.revision,
                          'productionTrusted': False, 'executorStarted': False}))
        return 0
    except Exception:
        print('{"status":"blocked","reason":"install-bundle-input-invalid"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
