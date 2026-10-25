"""SSHSIG documents with independent namespaces and fixed OpenSSH invocation."""
from pathlib import Path
import subprocess
import tempfile

from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json, signature_bytes, trust_keys

POLICY_NAMESPACE = 'portfolio-cd-policy-v1'
REQUEST_NAMESPACE = 'portfolio-cd-request-v1'


def authenticate(raw, *, keys, namespace, limit=131072):
    trust_keys(canonical({'version': 1, 'operators': keys}))
    document = closed_json(raw, limit)
    require(type(document) is dict and set(document) == {'payload', 'principal', 'signature'}, 'cd-signature-envelope-invalid')
    principal = document['principal']
    require(type(principal) is str and principal in keys, 'cd-principal-untrusted')
    message = canonical(document['payload'])
    signature = signature_bytes(document['signature'], expected_namespace=namespace)
    with tempfile.TemporaryDirectory(prefix='pd-cd-signature-') as temporary:
        directory = Path(temporary)
        (directory / 'signature').write_bytes(signature)
        (directory / 'signers').write_text(f'{principal} namespaces="{namespace}" {keys[principal]}\n')
        result = subprocess.run(['/usr/bin/ssh-keygen', '-q', '-Y', 'verify', '-f', str(directory / 'signers'),
                                 '-I', principal, '-n', namespace, '-s', str(directory / 'signature')],
                                input=message, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env={'HOME': temporary, 'LC_ALL': 'C'}, cwd=directory, timeout=5, check=False)
    require(result.returncode == 0, 'cd-signature-invalid')
    return document['payload'], principal


def sign(payload, *, principal, key, namespace):
    """Runner/workstation only. A private key path is never accepted by the bridge."""
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', namespace],
                            input=canonical(payload), capture_output=True, timeout=5, check=False)
    require(result.returncode == 0, 'cd-signing-failed')
    return canonical({'payload': payload, 'principal': principal, 'signature': result.stdout.decode('ascii')})
