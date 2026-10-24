"""Closed SSHSIG v1 approvals; trust comes only from sealed installation pins."""
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from scripts.docker_adapter import DockerError
from scripts.production_integration.authorization import ApprovalClaims
from scripts.production_integration.installation import SealedInstallation, protected

NAMESPACE = 'portfolio-deployment-operation-v1'
BINARY = Path('/usr/bin/ssh-keygen')
TRUST = Path('/etc/portfolio-deployment/operator-keys.json')
ENVELOPE_LIMIT = 8192
TRUST_LIMIT = 8192
SIGNATURE_LIMIT = 2048
FIELDS = {'version', 'candidate', 'operator', 'nonce', 'not_before', 'expires'}


def require(value):
    if not value:
        raise DockerError('production-operator-approval-invalid')


def canonical(value):
    """ASCII, sorted keys, compact separators, no trailing newline."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def closed_json(raw, limit):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    try:
        require(type(raw) is bytes and 0 < len(raw) <= limit)
        value = json.loads(raw.decode('ascii'), object_pairs_hook=unique)
        # Also rejects floats/exponents, alternative escapes, whitespace and -0.
        require(canonical(value) == raw)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise DockerError('production-operator-approval-invalid') from None


def canonical_payload(payload):
    require(type(payload) is dict and set(payload) == FIELDS)
    require(type(payload['version']) is int and payload['version'] == 1)
    for name, pattern in (('candidate', r'sha256:[0-9a-f]{64}'),
                          ('operator', r'[A-Za-z0-9@._-]{1,128}'), ('nonce', r'[0-9a-f]{32}')):
        require(type(payload[name]) is str and re.fullmatch(pattern, payload[name]))
    require(all(type(payload[k]) is int and 0 <= payload[k] <= 253402300799
                for k in ('not_before', 'expires')))
    require(0 < payload['expires'] - payload['not_before'] <= 3600)
    raw = canonical(payload)
    require(len(raw) <= 1024)
    return raw


def ed25519_key(raw):
    require(len(raw) == 51 and raw[:19] == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20')


def trust_keys(raw):
    document = closed_json(raw, TRUST_LIMIT)
    require(type(document) is dict and set(document) == {'version', 'operators'})
    require(type(document['version']) is int and document['version'] == 1)
    keys = document['operators']
    require(type(keys) is dict and 0 < len(keys) <= 16)
    for principal, key in keys.items():
        require(re.fullmatch(r'[A-Za-z0-9@._-]{1,128}', principal))
        require(type(key) is str and re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]{68}', key))
        try:
            ed25519_key(base64.b64decode(key.split(' ')[1], validate=True))
        except ValueError:
            raise DockerError('production-operator-approval-invalid') from None
    return keys


def signature_bytes(armor, *, expected_namespace=NAMESPACE):
    """Constrain wire algorithms before delegating cryptography to OpenSSH."""
    require(type(armor) is str and len(armor) <= SIGNATURE_LIMIT)
    require(re.fullmatch(r'-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+-----END SSH SIGNATURE-----\n', armor))
    try:
        wire = base64.b64decode(''.join(armor.splitlines()[1:-1]), validate=True)
        require(wire[:10] == b'SSHSIG\x00\x00\x00\x01')
        position = 10
        def string():
            nonlocal position
            size = struct.unpack('>I', wire[position:position + 4])[0]
            position += 4
            require(size <= len(wire) - position)
            value = wire[position:position + size]
            position += size
            return value
        key, namespace, reserved, algorithm, signature = (string() for _ in range(5))
        require(position == len(wire))
        ed25519_key(key)
        require(namespace == expected_namespace.encode() and reserved == b'' and algorithm == b'sha512')
        require(len(signature) == 83 and signature[:19] == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x40')
    except (ValueError, struct.error):
        raise DockerError('production-operator-approval-invalid') from None
    return armor.encode('ascii')


def authenticate_signed(envelope, trusted_keys):
    """Internal local verification. This creates claims, never execution authority."""
    document = closed_json(envelope, ENVELOPE_LIMIT)
    require(type(document) is dict and set(document) == {'payload', 'signature'})
    payload = document['payload']
    message = canonical_payload(payload)
    signature = signature_bytes(document['signature'])
    require(payload['operator'] in trusted_keys)
    key = trusted_keys[payload['operator']]
    try:
        # Private random workspace; no request-selected paths, commands or environment.
        with tempfile.TemporaryDirectory(prefix='pd-operator-approval-') as temporary:
            directory = Path(temporary)
            (directory / 'message').write_bytes(message)
            (directory / 'signature').write_bytes(signature)
            (directory / 'allowed_signers').write_text(
                f'{payload["operator"]} namespaces="{NAMESPACE}" {key}\n', encoding='ascii')
            with (directory / 'message').open('rb') as stream:
                result = subprocess.run(
                    [str(BINARY), '-q', '-Y', 'verify', '-f', str(directory / 'allowed_signers'),
                     '-I', payload['operator'], '-n', NAMESPACE, '-s', str(directory / 'signature')],
                    stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=directory, env={'HOME': temporary, 'LC_ALL': 'C'}, timeout=5, check=False)
            require(result.returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        raise DockerError('production-operator-approval-verifier-unavailable') from None
    return ApprovalClaims(payload['candidate'], payload['operator'], payload['not_before'],
                          payload['expires'], payload['nonce'])


class SSHSIGVerifier:
    """Fixed composition dependency, never constructed from an operation request.

    Both external files must be independently approved in SealedInstallation.files.
    Reverification on every authenticate includes the executor's under-lock review.
    """
    def __init__(self, installation):
        require(isinstance(installation, SealedInstallation))
        self.installation = installation

    def authenticate(self, envelope):
        self.installation.verify()
        pins = dict(self.installation.files)
        if not {str(BINARY), str(TRUST)} <= pins.keys():
            raise DockerError('production-operator-approval-trust-unconfigured')
        protected(TRUST)
        try:
            with TRUST.open('rb') as stream:
                raw = stream.read(TRUST_LIMIT + 1)
        except OSError:
            raise DockerError('production-operator-approval-trust-unconfigured') from None
        require(hashlib.sha256(raw).hexdigest() == pins[str(TRUST)])
        return authenticate_signed(envelope, trust_keys(raw))
