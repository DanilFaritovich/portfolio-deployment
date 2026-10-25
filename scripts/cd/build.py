"""Release-runner bundle build from exact Git objects and existing pinned assets."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile

from scripts.cd.policy import pin
from scripts.caddy_producer import read_public, extract_binary, wait_for_offline
from scripts.prepare_installation_vendor import prepare
from scripts.production_integration.install_bundle import build_bundle
from scripts.production_integration.observations import require
from scripts.production_integration.release_transport import GitHubRead, HTTPSRead


def build(repository, revision, output, bindings):
    repository, output = Path(repository), Path(output)
    require(not output.exists(), 'cd-release-output-already-exists')
    output.mkdir(mode=0o700)
    wheels = json.loads((repository / 'artifacts/c4.4/wheels.json').read_bytes())
    with tempfile.TemporaryDirectory(prefix='pd-cd-build-') as temporary:
        temporary = Path(temporary)
        wheelhouse = temporary / 'wheels'
        wheelhouse.mkdir()
        for entry in wheels['wheels']:
            raw = read_public(entry['url'], limit=16*1024*1024)
            require(pin(raw) == entry['sha256'], 'cd-wheel-hash-mismatch')
            (wheelhouse / entry['filename']).write_bytes(raw)
        vendor = temporary / 'vendor'
        prepare(wheelhouse, vendor, wheels)
        lock = json.loads((repository / 'images/caddy/inputs.lock.json').read_bytes())
        archive = read_public('https://github.com/caddyserver/caddy/releases/download/v' + lock['version'] +
                              '/caddy_' + lock['version'] + '_linux_amd64.tar.gz')
        require(pin(archive) == lock['archiveSha256'], 'cd-caddy-archive-mismatch')
        extract_binary(archive, lock, temporary)
        assets = {'vendor/' + p.relative_to(vendor).as_posix(): p.read_bytes() for p in vendor.rglob('*') if p.is_file()}
        assets['tools/caddy'] = (temporary / 'caddy').read_bytes()
        pins = {n: pin(raw) for n, raw in assets.items()}
        require({n: h for n, h in pins.items() if n.startswith('vendor/')} ==
                json.loads((repository / 'artifacts/c4.4/vendor-export.json').read_bytes())['files'], 'cd-vendor-export-drift')
        raw = build_bundle(repository, revision, assets, pins, bindings=bindings)
        require(build_bundle(repository, revision, assets, pins, bindings=bindings) == raw, 'cd-bundle-not-reproducible')
        (output / 'portfolio-deployment.zip').write_bytes(raw)
        (output / 'release.json').write_text(json.dumps({'manager_sha': revision, 'bundle_sha256': pin(raw)}))
        return pin(raw)


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('REF_PROTECTED') == 'true' and
            os.environ.get('GITHUB_REPOSITORY') == 'DanilFaritovich/portfolio-deployment', 'cd-build-protected-main-required')
    revision = os.environ['GITHUB_SHA']
    wait_for_offline(lambda: GitHubRead(os.environ['GH_TOKEN'], HTTPSRead()), revision)
    bindings = base64.b64decode(os.environ['CD_MANAGER_BINDINGS_B64'], validate=True)
    require(bool(bindings), 'cd-reviewed-bindings-required')
    digest = build(Path(__file__).resolve().parents[2], revision, Path(sys.argv[1]), bindings)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('bundle-digest=sha256:' + digest + '\n')


if __name__ == '__main__':
    main()
