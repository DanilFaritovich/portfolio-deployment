"""Offline export of the independently reviewed CD capsule and fixed SSH policy.

No installation, accounts, credentials, host contact or operation approval. The
separate administrator reviews the exact kit before invoking cd_setup.py.
"""
import argparse
import io
import json
from pathlib import Path
import stat
import zipfile

from scripts.cd.policy import pin
from installation.operator_bootstrap import CADDY_INPUTS
from scripts.production_integration.install_bundle import source_target
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json, trust_keys

CAPSULE = '/usr/local/lib/portfolio-deployment-cd/'


def export(repository, *, assets, bindings, external_pins, signed_policy):
    repository = Path(repository)
    files = {}
    for name in sorted(CADDY_INPUTS):
        path = repository / name
        require(path.is_file() and not path.is_symlink(), 'cd-capsule-caddy-input-required')
        files['capsule/' + name] = path.read_bytes()
    for directory in ('scripts', 'schemas'):
        for path in sorted((repository / directory).rglob('*')):
            if not path.is_file() or path.suffix == '.pyc':
                continue
            name = path.relative_to(repository).as_posix()
            if source_target(name) is not None or name in ('scripts/production_integration/install_bundle.py',
                                                         'scripts/production_integration/bundle_review.py'):
                require(not path.is_symlink(), 'cd-capsule-source-symlink')
                files['capsule/' + name] = path.read_bytes()
    files['capsule/installation/operator_bootstrap.py'] = (repository/'installation/operator_bootstrap.py').read_bytes()
    files['capsule/cd_launcher.py'] = (repository/'installation/cd_launcher.py').read_bytes()
    files['capsule/bootstrap_entry.py'] = (repository/'installation/bootstrap_entry.py').read_bytes()
    files['capsule/health_check.py'] = (repository/'installation/cd_health_check.py').read_bytes()
    files['capsule/cd_bindings.py'] = bindings
    for name, raw in assets.items():
        require(name.startswith('vendor/') or name == 'tools/caddy', 'cd-capsule-asset-rejected')
        files['capsule/' + name] = raw
    inventory = {CAPSULE + n.removeprefix('capsule/'): pin(raw) for n, raw in files.items()}
    require(type(external_pins) is dict and bool(external_pins) and
            all(type(p) is str and Path(p).is_absolute() and '..' not in Path(p).parts and
                not Path(p).is_relative_to('/opt/portfolio-deployment') and not p.startswith(CAPSULE) and
                type(h) is str and len(h)==64 for p,h in external_pins.items()), 'cd-capsule-external-pin-invalid')
    inventory.update(external_pins)
    files['capsule/capsule.json'] = canonical({'files': inventory})
    files['policy.json'] = signed_policy
    policy = closed_json(signed_policy, 131072)['payload']
    transport = {role: policy[role]['key'] for role in ('automation', 'observer')}
    trust_keys(canonical({'version': 1, 'operators': transport}))
    forced = '/usr/local/bin/portfolio-cd-forced-command.py'
    files['forced_command.py'] = (repository/'installation/cd_forced_command.py').read_bytes()
    files['authorized_keys'] = ''.join('restrict,command="/usr/bin/python3 -I -S -B ' + forced + ' ' +
        role + '" ' + key + '\n' for role, key in transport.items()).encode()
    commands = ', '.join('/usr/bin/python3 -I -S -B ' + CAPSULE + 'cd_launcher.py ' + role for role in transport)
    files['sudoers'] = ('Defaults:portfolio-cd env_reset\nportfolio-cd ALL=(root) NOPASSWD: ' + commands + '\n').encode()
    files['kit.json'] = canonical({'version': 1, 'files': {n: pin(raw) for n, raw in files.items()}})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(entry, raw)
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager-bundle', type=Path, required=True)
    parser.add_argument('--external-pins', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from installation.operator_bootstrap import read_bundle
    _, files = read_bundle(args.manager_bundle.read_bytes())
    raw = export(Path(__file__).resolve().parents[2],
        assets={n:v[0] for n,v in files.items() if n.startswith('vendor/') or n == 'tools/caddy'},
        bindings=files['bindings.py'][0], external_pins=json.loads(args.external_pins.read_bytes()),
        signed_policy=args.policy.read_bytes())
    with args.output.open('xb') as output:
        output.write(raw)
    print(json.dumps({'kit_sha256': pin(raw), 'productionInstalled': False}))


if __name__ == '__main__':
    main()
