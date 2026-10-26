#!/usr/bin/python3
"""Independent capsule entry into the unchanged sealed first-phase executor."""
import base64
from dataclasses import asdict
import os
from pathlib import Path
import stat
import sys


def run(request, installation, executor):
    SCOPES = ('first-bootstrap', 'prepare-project-router-image', 'first-project-router-rollout')
    from scripts.production_integration.authorization import OperationCandidate, review_approval
    from scripts.production_integration.locking import production_host_lock
    from scripts.production_integration.observations import require
    require(request['scope'] in SCOPES and request['manager_sha'] == installation.manager_sha,
            'bootstrap-installed-manager-mismatch')
    installation.verify()
    if request['op'] == 'bootstrap-prepare':
        with production_host_lock() as lease:
            prepared = executor.preflight.prepare_locked(request['scope'], lease)
        return {'status': 'prepared', 'candidate': asdict(prepared.candidate)}
    require(request['op'] == 'bootstrap-execute', 'bootstrap-operation-forbidden')
    candidate = OperationCandidate(**request['candidate'])
    candidate.validate()
    require(candidate.scope == request['scope'] and candidate.manager_sha == installation.manager_sha,
            'bootstrap-candidate-mismatch')
    envelope = base64.b64decode(request['envelope'], validate=True)
    require(0 < len(envelope) <= 65536, 'bootstrap-envelope-invalid')
    # The installed verifier trusts operators only. Delegated CD signatures do
    # not acquire first-phase authority. Executor rechecks everything under flock,
    # journals intent/nonce before mutation and retains failure/recovery fences.
    review_approval(candidate, envelope, verifier=executor.verifier, now=executor.clock())
    result = executor.run(candidate, envelope)
    return {'status': result.status, 'candidate': result.candidate, 'reasons': result.reasons}


def main():
    if (len(sys.argv) != 1 or os.geteuid() != 0 or not sys.flags.isolated or
        not sys.flags.no_site or not sys.dont_write_bytecode):
        return 2
    root = Path('/opt/portfolio-deployment')
    for path in (Path('/'), Path('/opt'), root, root / 'vendor'):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return 2
    sys.path[0:0] = [str(root), str(root / 'vendor')]
    from scripts.production_integration.installation import SealedInstallation, protected
    from scripts.production_integration.operator_approval import closed_json, canonical
    protected(root / 'installation.json')
    document = closed_json((root / 'installation.json').read_bytes(), 2 * 1024 * 1024)
    installation = SealedInstallation(document['manager_sha'], tuple(map(tuple, document['files'])))
    installation.verify()
    request = closed_json(sys.stdin.buffer.read(131073), 131072)
    import bindings
    from scripts.cd.diagnostics import blocked
    try:
        result = run(request, installation, bindings.build(installation))
    except Exception as error:
        result = blocked(error, 'bootstrap-installed-entry-failed')
    print(canonical(result).decode())
    return 0 if result['status'] in ('prepared', 'succeeded') else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"status":"blocked","reason":"bootstrap-installed-entry-failed"}')
        sys.exit(2)
