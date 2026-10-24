"""Short-lived off-VDS probe receipt, authenticated by a separate SSHSIG key.

The independently operated external probe runs the same fixed DNS/TLS/HTTP checks
and signs the exact subject only after success. Its private key never reaches VDS.
Receipt publication grants no deployment or administrative authority.
"""
from pathlib import Path
import subprocess
import tempfile
import time

from scripts.docker_adapter import DockerError
from scripts.production_integration.installation import protected
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import closed_json, signature_bytes, trust_keys
from scripts.reconciliation import encode

RECEIPT = Path('/var/lib/portfolio-deployment-public/observation.json')
BINARY = '/usr/bin/ssh-keygen'
NAMESPACE = 'portfolio-public-https-v1'


class SignedPublicWitness:
    def __init__(self, installation, public_key, *, clock=time.time):
        trust_keys(encode({'version': 1, 'operators': {'public-observer': public_key}}).encode())
        self.installation, self.public_key, self.clock = installation, public_key, clock

    def verify(self, subject, *, timeout=0):
        # A first-route observer cannot sign before activation. Allow only a
        # bounded rendezvous within the caller's remaining public-probe budget.
        require(type(timeout) in (int, float) and 0 <= timeout <= 15,
                'public-witness-wait-invalid')
        deadline = time.monotonic() + timeout
        while True:
            budget = deadline - time.monotonic()
            require(not timeout or budget > 0, 'public-witness-delivery-timeout')
            try:
                result = self._verify(subject, deadline if timeout else None)
                require(not timeout or time.monotonic() < deadline, 'public-witness-delivery-timeout')
                return result
            except DockerError as error:
                if error.code not in ('production-installation-not-provisioned',
                                      'public-witness-stale-or-unbound'):
                    raise
                budget = deadline - time.monotonic()
                if budget <= 0:
                    raise
                time.sleep(min(0.1, budget))

    def _verify(self, subject, deadline):
        self.installation.verify()
        require(BINARY in dict(self.installation.files), 'public-witness-verifier-unpinned')
        protected(RECEIPT)
        try:
            with RECEIPT.open('rb') as stream:
                document = closed_json(stream.read(16385), 16384)
            require(type(document) is dict and set(document) == {'payload', 'signature'}, 'public-witness-invalid')
            payload = document['payload']
            require(type(payload) is dict and set(payload) == {'subject', 'observed_at', 'expires'} and
                    payload['subject'] == subject and type(payload['observed_at']) is int and
                    type(payload['expires']) is int and
                    0 <= self.clock() - payload['observed_at'] <= 30 and
                    payload['observed_at'] < payload['expires'] <= payload['observed_at'] + 30 and
                    self.clock() < payload['expires'], 'public-witness-stale-or-unbound')
            message = encode(payload).encode()
            signature = signature_bytes(document['signature'], expected_namespace=NAMESPACE)
            with tempfile.TemporaryDirectory(prefix='pd-public-witness-') as directory:
                root = Path(directory)
                (root / 'message').write_bytes(message)
                (root / 'signature').write_bytes(signature)
                (root / 'signers').write_text(f'public-observer namespaces="{NAMESPACE}" {self.public_key}\n')
                with (root / 'message').open('rb') as stream:
                    signature_timeout = 2 if deadline is None else min(2, deadline - time.monotonic())
                    require(signature_timeout > 0, 'public-witness-delivery-timeout')
                    result = subprocess.run([BINARY, '-q', '-Y', 'verify', '-f', str(root / 'signers'),
                        '-I', 'public-observer', '-n', NAMESPACE, '-s', str(root / 'signature')],
                        stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        cwd=directory, env={'HOME': directory, 'LC_ALL': 'C'},
                        timeout=signature_timeout, check=False)
                require(result.returncode == 0, 'public-witness-signature-invalid')
            require(self.clock() < payload['expires'], 'public-witness-stale-or-unbound')
        except DockerError:
            raise
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
            raise DockerError('public-witness-unavailable') from None
        return subject
