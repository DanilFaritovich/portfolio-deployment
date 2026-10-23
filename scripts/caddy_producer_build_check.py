"""Validate local build config before upload; this is not OCI/provenance proof."""
import argparse
import json
import re

from scripts.caddy_producer import load_lock
from scripts.production_integration.caddy_image import COMMAND
from scripts.production_integration.release_transport import require


def check(document):
    require(isinstance(document, list) and len(document) == 1, 'caddy-build-config-invalid')
    image = document[0]
    config = image['Config']
    lock = load_lock()
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']) is not None and
            image['Os'] == 'linux' and image['Architecture'] == 'amd64' and
            config.get('Entrypoint') in (None, []) and config.get('Cmd') == COMMAND and
            config.get('User') == '0:0' and config.get('WorkingDir') == '/srv' and
            config.get('ExposedPorts') == {'80/tcp': {}, '443/tcp': {}} and
            {'XDG_CONFIG_HOME=/config', 'XDG_DATA_HOME=/data', 'CADDY_VERSION=v' + lock['version']}.issubset(
                config.get('Env') or ()) and not config.get('Volumes') and not config.get('Healthcheck'),
            'caddy-build-contract-mismatch')
    return image['Id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inspect')
    parser.add_argument('--expected')
    args = parser.parse_args()
    from pathlib import Path
    doc = json.loads(Path(args.inspect).read_bytes())
    identity = check(doc)
    if args.expected:
        expected = json.loads(Path(args.expected).read_bytes())
        require(identity == check(expected) and doc[0]['Config'] == expected[0]['Config'], 'caddy-build-changed')


if __name__ == '__main__':
    main()
