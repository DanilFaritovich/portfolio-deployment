#!/usr/bin/python3 -I
"""Install only at /opt/portfolio-deployment/launcher.py; never run a checkout."""
import base64
import json
import os
from pathlib import Path
import stat
import sys


def main():
    # Before importing ANY bundled Python: validate protected path ancestry.
    if len(sys.argv) != 1 or os.geteuid() != 0 or not sys.flags.isolated or not sys.flags.no_site:
        return 2
    bundle = Path('/opt/portfolio-deployment')
    for path in (Path('/'), Path('/opt'), bundle, bundle / 'vendor'):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return 2
    sys.dont_write_bytecode = True
    sys.path[0:0] = [str(bundle), str(bundle / 'vendor')]
    from scripts.production_integration.installation import SealedInstallation, protected
    from scripts.planning_inputs import parse_snapshot
    from scripts.production_integration.authorization import OperationCandidate
    try:
        protected(bundle / 'installation.json')
        with (bundle / 'installation.json').open('rb') as stream:
            raw = stream.read(2097153)
        document = parse_snapshot(raw)
        installation = SealedInstallation(document['manager_sha'], tuple(tuple(p) for p in document['files']))
        installation.verify()
        # Fixed protected spool: infrastructure authenticates submission/copy.
        # Only candidate + signed envelope, never Python objects or module names.
        request = Path('/srv/portfolio/requests/operation.json')
        protected(request)
        with request.open('rb') as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            return 2
        data = parse_snapshot(raw)
        if set(data) != {'candidate', 'envelope'}:
            return 2
        envelope = base64.b64decode(data['envelope'], validate=True)
        if not 0 < len(envelope) <= 65536:
            return 2
        candidate = OperationCandidate(**data['candidate'])
        candidate.validate()
        # This ONE fixed installed module is part of the sealed bundle inventory.
        # Its default blocks; no module name/environment/JSON selects wiring.
        import bindings
        executor = bindings.build(installation)
        result = executor.run(candidate, envelope)
        print(json.dumps({'status': result.status, 'candidate': result.candidate,
                          'productionAuthorized': result.status == 'succeeded',
                          'reasons': result.reasons}))
        return 0 if result.status == 'succeeded' else 2
    except Exception:
        print('{"status":"blocked","productionAuthorized":false,"reasons":["production-installed-dependency-unavailable"]}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
