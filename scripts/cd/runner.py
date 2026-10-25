"""Ephemeral Actions SSH client and independent public HTTPS witness."""
import base64
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from scripts.cd.policy import pin
from scripts.cd.release import ImageRelease
from scripts.cd.signatures import sign, REQUEST_NAMESPACE
from scripts.gateway import GatewayRoute
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import NAMESPACE, canonical
from scripts.production_integration.public_postconditions import PublicPolicy, PublicPostconditions
from scripts.production_integration.public_witness import NAMESPACE as OBSERVER_NAMESPACE
from scripts.production_integration.candidate import hashed


class Client:
    def __init__(self, directory, *, roles=('automation', 'observer')):
        self.directory = Path(directory)
        host, user = os.environ['CD_HOST'], os.environ['CD_USER']
        require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', host) and
                re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user) and user != 'root', 'cd-ssh-target-invalid')
        self.target = user + '@' + host
        self.keys = {}
        for role in roles:
            path = self.directory / (role + '.key')
            path.write_bytes(base64.b64decode(os.environ['CD_' + role.upper() + '_KEY_B64'], validate=True))
            path.chmod(0o600)
            self.keys[role] = path
        known = self.directory / 'known_hosts'
        known.write_bytes(base64.b64decode(os.environ['CD_KNOWN_HOSTS_B64'], validate=True))
        require(known.stat().st_size > 0, 'cd-known-host-required')
        self.options = ['ssh', '-F', '/dev/null', '-oBatchMode=yes', '-oIdentitiesOnly=yes', '-oIdentityAgent=none',
            '-oStrictHostKeyChecking=yes', '-oUserKnownHostsFile=' + str(known), '-oClearAllForwardings=yes',
            '-oRequestTTY=no', '-oPermitLocalCommand=no', '-oProxyCommand=none', '-oConnectTimeout=10']

    def call(self, request, *, role='automation', raw=b'', timeout=1500):
        result = subprocess.run([*self.options, '-i', str(self.keys[role]), self.target, 'portfolio-cd-v1'],
            input=canonical(request) + b'\n' + raw, capture_output=True, timeout=timeout, check=False)
        require(len(result.stdout) <= (2*1024*1024 if role=='observer' else 131072) and result.stdout,
                'cd-ssh-result-unavailable')
        doc = json.loads(result.stdout)
        require(type(doc) is dict, 'cd-ssh-result-invalid')
        return doc

    def approval(self, candidate):
        now = int(time.time())
        payload = {'version': 1, 'candidate': hashed(candidate), 'operator': os.environ['CD_AUTOMATION_PRINCIPAL'],
                   'nonce': uuid.uuid4().hex, 'not_before': now - 5, 'expires': now + 295}
        signed = json.loads(sign(payload, principal=payload['operator'], key=self.keys['automation'], namespace=NAMESPACE))
        signed.pop('principal')
        return base64.b64encode(canonical(signed)).decode()

    def context(self, candidate, envelope):
        now = int(time.time())
        payload = {'approval_sha256': pin(base64.b64decode(envelope)), 'candidate': hashed(candidate),
                   'repository': os.environ['GITHUB_REPOSITORY'], 'source_sha': os.environ['GITHUB_SHA'],
                   'run_id': int(os.environ['GITHUB_RUN_ID']), 'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
                   'not_before': now-5, 'expires': now+295}
        return base64.b64encode(sign(payload, principal=os.environ['CD_AUTOMATION_PRINCIPAL'],
            key=self.keys['automation'], namespace=REQUEST_NAMESPACE)).decode()

    def observer(self, stop, observed=None):
        while not stop.wait(.25):
            try:
                challenge = self.call({'op': 'observe'}, role='observer', timeout=10)
                require(challenge['policy_id'] == os.environ['CD_POLICY_ID'] and
                        0 <= time.time() - challenge['created'] <= 15, 'cd-observer-policy-mismatch')
                document = challenge['policy']
                document['identity'] = tuple(document['identity'])
                document['ipv4'], document['ipv6'] = tuple(document['ipv4']), tuple(document['ipv6'])
                public = PublicPolicy(**document)
                ca = base64.b64decode(challenge['ca'], validate=True)
                require(pin(ca) == public.ca_sha256, 'cd-observer-ca-mismatch')
                routes = tuple(GatewayRoute(**r) for r in challenge['routes'])
                with tempfile.TemporaryDirectory(prefix='pd-cd-observer-') as temporary:
                    path = Path(temporary) / 'ca.pem'
                    path.write_bytes(ca)
                    subject = PublicPostconditions(replace(public, ca_file=str(path)), external=None).probe(routes, public.identity)
                subject['policy'] = hashed(asdict(public))
                now = int(time.time())
                signed = json.loads(sign({'subject': subject, 'observed_at': now, 'expires': now + 30},
                    principal=os.environ['CD_OBSERVER_PRINCIPAL'], key=self.keys['observer'], namespace=OBSERVER_NAMESPACE))
                signed.pop('principal')
                result = self.call({'op': 'witness', 'receipt': signed}, role='observer', timeout=10)
                if result.get('status') == 'observed' and observed is not None:
                    observed.set()
            except Exception:
                # An absent challenge or failed HTTPS yields NO success receipt.
                # Required server-side acceptance will time out and fail release.
                continue


def deploy(client, kind, directory):
    directory = Path(directory)
    if kind == 'manager':
        raw = (directory / 'portfolio-deployment.zip').read_bytes()
        response = client.call({'op': 'manager-prepare', 'length': len(raw)}, raw=raw)
        require(response.get('status') == 'prepared', 'cd-manager-prepare-failed')
        candidate = response['candidate']
        envelope = client.approval(candidate)
        response = client.call({'op': 'manager-execute', 'candidate': candidate, 'envelope': envelope,
                               'context': client.context(candidate, envelope)})
        require(response.get('status') in ('succeeded', 'no-op'), 'cd-manager-deployment-failed')
        return response
    require(kind == 'application', 'cd-deployment-kind-invalid')
    content = json.loads((directory / 'public-content.json').read_bytes())
    mapping = json.loads((directory / 'mapping.json').read_bytes())
    release = ImageRelease(content['sourceSha'], mapping['image'], content['htmlSha256'], content['runId'], content['runAttempt']).validate()
    require(mapping['image'].split('@')[1] == content['imageDigest'] and content['sourceRef'] == 'refs/heads/main' and
            content['sourceRepository'] == 'DanilFaritovich/project-router', 'cd-release-evidence-mismatch')
    for operation in ('prepare-cache', 'prepare-update'):
        response = client.call({'op': 'application', 'request': {'op': operation, 'release': asdict(release)}})
        if response.get('status') in ('cache-present', 'no-op'):
            continue
        require(response.get('status') == 'prepared', 'cd-application-prepare-failed')
        candidate = response['candidate']
        envelope = client.approval(candidate)
        response = client.call({'op': 'application', 'request': {'op': 'execute', 'release': asdict(release),
                               'candidate': candidate, 'envelope': envelope}, 'context': client.context(candidate, envelope)})
        require(response.get('status') == 'succeeded', 'cd-application-deployment-failed')
    return response


def main():
    observe_only = sys.argv[1] == 'observe-initial'
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            (os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run' or observe_only and
             os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
             os.environ.get('GITHUB_REF') == 'refs/heads/main'), 'cd-trusted-workflow-required')
    with tempfile.TemporaryDirectory(prefix='pd-cd-client-') as temporary:
        client = Client(temporary, roles=('observer',) if observe_only else ('automation','observer'))
        stop = threading.Event()
        observed = threading.Event()
        thread = threading.Thread(target=client.observer, args=(stop,observed), daemon=True)
        thread.start()
        try:
            if observe_only:
                require(observed.wait(timeout=1200), 'cd-initial-https-not-observed')
                result = {'status': 'observed'}
            else:
                result = deploy(client, sys.argv[1], sys.argv[2])
        finally:
            stop.set()
            thread.join(timeout=30)
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('cd-deployment-failed; inspect bounded host receipts')
        sys.exit(1)
