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

from scripts.docker_adapter import DockerError
from scripts.cd.policy import pin
from scripts.cd.release import ImageRelease
from scripts.cd.signatures import sign, REQUEST_NAMESPACE
from scripts.gateway import GatewayRoute
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import NAMESPACE, canonical
from scripts.production_integration.public_postconditions import PublicPolicy, PublicPostconditions
from scripts.production_integration.public_witness import NAMESPACE as OBSERVER_NAMESPACE
from scripts.production_integration.candidate import hashed


DIAGNOSTICS = {
    'cd-ssh-port-invalid': 'configure CD_PORT as a decimal SSH port between 1 and 65535',
    'cd-policy-id-required': 'configure CD_POLICY_ID from the exact operator-approved signed standing policy; complete administrative bootstrap first',
    'cd-ssh-authentication-failed': 'SSH authentication failed; check the restricted account/key installation',
    'cd-ssh-host-key-mismatch': 'SSH host identity was rejected; verify pinned host keys independently',
    'cd-ssh-connection-failed': 'SSH could not reach the configured host',
    'cd-ssh-transport-failed': 'SSH failed; inspect bounded connection diagnostics through the operator channel',
    'cd-ssh-timeout': 'SSH timed out; remote outcome may be unknown; inspect host receipts before any retry',
    'cd-ssh-result-unavailable': 'SSH returned no bounded result; inspect host receipts before any retry',
    'cd-ssh-result-invalid': 'SSH returned an invalid result; inspect host receipts before any retry',
    'cd-remote-intake-blocked': 'the independent host intake blocked the request; review capsule, installation and authority prerequisites',
    'cd-remote-recovery-required': 'the host reported recovery or rollback; inspect retained receipts; do not replay',
    'cd-remote-command-failed': 'the remote command failed or blocked; inspect retained host receipts',
}


def require_policy_id():
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', os.environ.get('CD_POLICY_ID', '')) is not None,
            'cd-policy-id-required')


def report_failure(error, operation='cd-deployment'):
    # Only reviewed fixed messages reach Actions logs. Neither SSH stderr nor
    # response bodies, traceback strings or environment values are diagnostics.
    code = str(error) if isinstance(error, DockerError) else ''
    if code in DIAGNOSTICS:
        print(operation + '-failed; ' + code + '; ' + DIAGNOSTICS[code])
    else:
        print(operation + '-failed; inspect bounded host receipts; do not replay unknown outcomes')


class Client:
    def __init__(self, directory, *, roles=('automation', 'observer')):
        self.directory = Path(directory)
        host, user = os.environ['CD_HOST'], os.environ['CD_USER']
        require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', host) and
                re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user) and user != 'root', 'cd-ssh-target-invalid')
        port = os.environ.get('CD_PORT') or '22'
        require(re.fullmatch(r'[1-9][0-9]{0,4}', port) is not None and int(port) <= 65535,
                'cd-ssh-port-invalid')
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
        self.options = ['ssh', '-F', '/dev/null', '-p', port, '-oBatchMode=yes', '-oIdentitiesOnly=yes', '-oIdentityAgent=none',
            '-oStrictHostKeyChecking=yes', '-oUserKnownHostsFile=' + str(known), '-oClearAllForwardings=yes',
            '-oRequestTTY=no', '-oPermitLocalCommand=no', '-oProxyCommand=none', '-oConnectTimeout=10']

    def call(self, request, *, role='automation', raw=b'', timeout=1500):
        try:
            result = subprocess.run([*self.options, '-i', str(self.keys[role]), self.target, 'portfolio-cd-v1'],
                input=canonical(request) + b'\n' + raw, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DockerError('cd-ssh-timeout') from None
        if result.returncode == 255:
            stderr = result.stderr
            if b'Permission denied' in stderr:
                code = 'cd-ssh-authentication-failed'
            elif any(p in stderr for p in (b'Host key verification failed', b'REMOTE HOST IDENTIFICATION HAS CHANGED')):
                code = 'cd-ssh-host-key-mismatch'
            elif any(p in stderr for p in (b'Connection refused', b'Connection timed out', b'No route to host', b'Could not resolve hostname')):
                code = 'cd-ssh-connection-failed'
            else:
                code = 'cd-ssh-transport-failed'
            raise DockerError(code)
        require(len(result.stdout) <= (2*1024*1024 if role=='observer' else 131072) and result.stdout,
                'cd-ssh-result-unavailable')
        try:
            doc = json.loads(result.stdout)
        except (ValueError, UnicodeError, RecursionError):
            raise DockerError('cd-ssh-result-invalid') from None
        require(type(doc) is dict, 'cd-ssh-result-invalid')
        if doc.get('status') in ('recovery-required', 'rolled-back'):
            raise DockerError('cd-remote-recovery-required')
        if doc.get('status') == 'blocked' or result.returncode != 0:
            if doc.get('reason') in ('cd-trusted-intake-failed', 'cd-installed-entry-failed', 'bootstrap-installed-entry-failed'):
                raise DockerError('cd-remote-intake-blocked')
            raise DockerError('cd-remote-command-failed')
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
    kind = sys.argv[1]
    observe_only = kind == 'observe-initial'
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            (os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run' or observe_only and
             os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
             os.environ.get('GITHUB_REF') == 'refs/heads/main'), 'cd-trusted-workflow-required')
    require_policy_id()
    with tempfile.TemporaryDirectory(prefix='pd-cd-client-') as temporary:
        roles = ('automation',)
        if kind != 'manager':
            roles = ('observer',) if observe_only else ('automation','observer')
        client = Client(temporary, roles=roles)
        stop = threading.Event()
        observed = threading.Event()
        thread = None
        # Manager upgrades verify sealed trees/imports and never serve public
        # traffic. Observer intake competes with their bounded host observations.
        if kind != 'manager':
            thread = threading.Thread(target=client.observer, args=(stop,observed), daemon=True)
            thread.start()
        try:
            if observe_only:
                require(observed.wait(timeout=1200), 'cd-initial-https-not-observed')
                result = {'status': 'observed'}
            else:
                result = deploy(client, kind, sys.argv[2])
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=30)
        print(json.dumps(result))


def entrypoint():
    try:
        main()
        return 0
    except Exception as error:
        report_failure(error)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
