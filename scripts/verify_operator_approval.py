"""Workstation-only signature inspection; no executor or host observation imports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from scripts.docker_adapter import DockerError
from scripts.production_integration.operator_approval import (
    BINARY, ENVELOPE_LIMIT, TRUST_LIMIT, authenticate_signed, trust_keys,
)


def pinned(path, expected, limit):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise DockerError('production-operator-offline-pin-invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or hashlib.sha256(raw).hexdigest() != expected:
        raise DockerError('production-operator-offline-pin-mismatch')
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--envelope', required=True, type=Path)
    parser.add_argument('--trust', required=True, type=Path)
    parser.add_argument('--trust-sha256', required=True)
    parser.add_argument('--ssh-keygen-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        pinned(BINARY, args.ssh_keygen_sha256, 16 * 1024 * 1024)
        keys = trust_keys(pinned(args.trust, args.trust_sha256, TRUST_LIMIT))
        with args.envelope.open('rb') as stream:
            envelope = stream.read(ENVELOPE_LIMIT + 1)
        claims = authenticate_signed(envelope, keys)
        if not claims.not_before <= time.time() < claims.expires:
            raise DockerError('production-approval-stale-or-binding-invalid')
        print(json.dumps({'status': 'signature-verified', 'candidate': claims.candidate,
                          'productionAuthorized': False}))
        return 0
    except (DockerError, OSError):
        print('{"status":"blocked","productionAuthorized":false}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
