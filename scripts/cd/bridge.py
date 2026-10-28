"""Fixed SSH intake. Installed independently, outside the replaceable Manager."""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from installation.operator_bootstrap import LIMIT, exclusive
from scripts.cd.application import installed_policy, release_ports
from scripts.cd.manager_release import ManagerReleaseVerifier
from scripts.cd.context import verify_context
from scripts.cd.policy import pin
from scripts.cd.signatures import authenticate
from scripts.cd.upgrade import ManagerUpdater, ROOT, STATE, verify_tree
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.gateway_write import durable_replace
from scripts.production_integration.installation import SealedInstallation, protected
from scripts.production_integration.locking import production_host_lock
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json, authenticate_signed
from scripts.production_integration.public_witness import NAMESPACE as OBSERVER_NAMESPACE
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.fixed_bindings import GH, ROOT as SIGSTORE_ROOT

CAPSULE = Path('/usr/local/lib/portfolio-deployment-cd')
HEADER_LIMIT = 131072
OBSERVER_LIMIT = 2 * 1024 * 1024  # includes a <=1MiB explicitly pinned CA bundle
OBSERVER_WAIT_SECONDS = 1200
PUBLIC_DIRECTORY = Path('/var/lib/portfolio-deployment-public')


def installation():
    protected(ROOT / 'installation.json')
    doc = json.loads((ROOT / 'installation.json').read_bytes())
    result = SealedInstallation(doc['manager_sha'], tuple(map(tuple, doc['files'])))
    result.verify()
    return result


def manager_health(root, revision):
    verify_tree(root)
    # The independently installed probe imports the reviewed installed tree ONLY
    # after verifier checks; it cannot execute an executor or process requests.
    result = subprocess.run(['/usr/bin/python3', '-I', '-S', '-B', str(CAPSULE / 'health_check.py')],
                            capture_output=True, env={'LANG': 'C'}, cwd='/', timeout=30, check=False)
    if result.returncode == 0:
        return result.stdout == (revision + '\n').encode()
    if result.returncode == 2:
        return False
    return None


def manager_verifier(policy):
    # This copy is an approved capsule binding, not the candidate's bindings.py.
    import cd_bindings
    fixed = cd_bindings.POLICY
    github, _, _, _ = release_ports(fixed)
    crypto = AttestationVerifier(GH, fixed.gh_sha256, SIGSTORE_ROOT, fixed.sigstore_root_sha256,
                                repository='DanilFaritovich/portfolio-deployment', subject_limit=LIMIT)
    return ManagerReleaseVerifier(policy, github, crypto)


def updater(installed, policy):
    def quiescent(lease):
        from scripts.cd.quiescence import check
        check(manager, lease)
    manager = ManagerUpdater(policy=policy, installation=installed, bundle_verifier=manager_verifier(policy),
        lock=production_host_lock, quiescent=quiescent, health=manager_health)
    return manager


def wait_for_challenge(not_before):
    # Read only the root-owned challenge while preflight collects host facts.
    # Installation/policy verification still runs before returning any evidence.
    require(type(not_before) is int and not_before > 0, 'cd-observer-wait-invalid')
    deadline = time.monotonic() + OBSERVER_WAIT_SECONDS
    path = STATE / 'challenge.json'
    while time.monotonic() < deadline:
        if path.exists():
            protected(path)
            challenge = closed_json(path.read_bytes(), OBSERVER_LIMIT)
            created = challenge.get('created')
            if type(created) is int and created >= not_before and 0 <= time.time() - created <= 15:
                return
        time.sleep(.25)
    require(False, 'cd-observer-wait-timeout')


def observe(policy):
    path = STATE / 'challenge.json'
    if not path.exists():
        # One-time operator-approved first rollout uses the existing production
        # executor/PublicPostconditions. Supply its independently pinned subject
        # to the same external runner; the observer still must prove live HTTPS.
        import cd_bindings
        from scripts.production_integration.candidate import InstalledContentReader
        from scripts.gateway import desired_routes
        fixed = cd_bindings.POLICY
        public = asdict(fixed.public)
        public['identity'] = list(public['identity'])
        public['ipv4'], public['ipv6'] = list(public['ipv4']), list(public['ipv6'])
        routes = desired_routes(InstalledContentReader(installation())()[0])
        return {'policy': public, 'routes': [asdict(r) for r in routes],
                'ca': base64.b64encode(Path(fixed.public.ca_file).read_bytes()).decode(),
                'created': int(time.time()), 'policy_id': policy.identity}
    protected(path)
    challenge = closed_json(path.read_bytes(), OBSERVER_LIMIT)
    require(challenge['policy_id'] == policy.identity and type(challenge['created']) is int and
            0 <= time.time() - challenge['created'] <= 15, 'cd-observer-challenge-stale')
    return challenge


def witness(policy, document):
    challenge = observe(policy)
    role = policy.document['observer']
    raw = canonical({'payload': document['payload'], 'signature': document['signature'], 'principal': role['principal']})
    payload, _ = authenticate(raw, keys={role['principal']: role['key']}, namespace=OBSERVER_NAMESPACE)
    from scripts.production_integration.candidate import hashed
    p = challenge['policy']
    expected = {'origin': 'https://danil-miftakhov.dev', 'path': '/en/', 'identity': p['identity'],
                'routes': challenge['routes'], 'policy': hashed(p)}
    require(set(payload) == {'subject', 'observed_at', 'expires'} and payload['subject'] == expected and
            type(payload['observed_at']) is int and type(payload['expires']) is int and
            0 <= time.time() - payload['observed_at'] <= 30 and
            time.time() < payload['expires'] <= payload['observed_at'] + 30, 'cd-observer-receipt-invalid')
    durable_replace(PUBLIC_DIRECTORY, 'observation.json', canonical(document))
    return {'status': 'observed'}


def publish_request(policy, candidate, envelope, request):
    role=policy.document['automation']
    claims=authenticate_signed(envelope,{role['principal']:role['key']})
    require(claims.candidate==candidate.identity and claims.not_before<=time.time()<claims.expires and
            claims.expires-claims.not_before<=300,'cd-request-approval-invalid')
    directory=STATE/'requests'
    protected(directory,directory=True)
    require(len(list(directory.iterdir()))<10000,'cd-request-retention-required')
    raw=canonical(request)
    require(len(raw)<=32768,'cd-request-size-exceeded')
    # A complete authenticated request is durably published before invoking any
    # privileged component. O_EXCL rejects duplicate nonces even across scopes.
    exclusive(directory/(claims.nonce+'.json'),raw)


def handle(role, request, stream):
    require(role in ('automation', 'observer') and type(request) is dict and type(request.get('op')) is str,
            'cd-bridge-request-invalid')
    if role == 'automation' and request['op'] == 'bootstrap-install':
        from scripts.cd.bootstrap import install
        return install(request, stream)
    if role == 'observer' and request['op'] == 'observe-wait':
        require(set(request) == {'op', 'not_before'}, 'cd-observer-scope-forbidden')
        wait_for_challenge(request['not_before'])
    installed = installation()
    policy = installed_policy(installed)
    if role == 'observer':
        if request['op'] == 'observe-wait':
            return observe(policy)
        require(set(request) in ({'op'}, {'op', 'receipt'}) and request['op'] in ('observe', 'witness'),
                'cd-observer-scope-forbidden')
        return observe(policy) if request['op'] == 'observe' else witness(policy, request['receipt'])
    manager = updater(installed, policy)
    manager.fenced()
    if request['op'] in ('bootstrap-prepare', 'bootstrap-execute'):
        from scripts.cd.bootstrap import operation
        return operation(request)
    if request['op'] == 'application':
        require(set(request) in ({'op', 'request'}, {'op', 'request', 'context'}), 'cd-application-fields-invalid')
        if request['request'].get('op') == 'execute':
            candidate = OperationCandidate(**request['request']['candidate'])
            verify_context(base64.b64decode(request['context'], validate=True),
                base64.b64decode(request['request']['envelope'], validate=True), candidate,
                policy=policy, github=manager.verifier.github, kind='application')
            publish_request(policy,candidate,base64.b64decode(request['request']['envelope'],validate=True),request)
        result = subprocess.run(['/usr/bin/python3', '-I', '-S', '-B', str(ROOT / 'cd_entry.py')],
            input=canonical(request['request']), capture_output=True, env={'LANG': 'C'}, cwd='/', timeout=1500, check=False)
        require(len(result.stdout) <= HEADER_LIMIT, 'cd-installed-output-limit')
        return closed_json(result.stdout.strip(), HEADER_LIMIT) if result.stdout else {'status': 'recovery-required'}
    if request['op'] == 'manager-prepare':
        require(set(request) == {'op', 'length'} and type(request['length']) is int and 0 < request['length'] <= LIMIT,
                'cd-bundle-length-invalid')
        raw = stream.read(request['length'] + 1)
        require(len(raw) == request['length'], 'cd-bundle-framing-invalid')
        with production_host_lock() as lease:
            manager.quiescent(lease)
            candidate = manager.prepare(raw)
            path = STATE / ('bundle-' + pin(raw) + '.zip')
            require(len(list(STATE.glob('bundle-*.zip'))) < 32 or path.exists(), 'cd-bundle-retention-required')
            if path.exists():
                protected(path)
                require(pin(path.read_bytes()) == pin(raw), 'cd-stored-bundle-changed')
            else:
                exclusive(path, raw)
        return {'status': 'prepared', 'candidate': asdict(candidate)}
    if request['op'] == 'manager-execute':
        require(set(request) == {'op', 'candidate', 'envelope', 'context'}, 'cd-manager-execute-fields-invalid')
        candidate = OperationCandidate(**request['candidate'])
        candidate.validate()
        verify_context(base64.b64decode(request['context'], validate=True),
                       base64.b64decode(request['envelope'], validate=True), candidate,
                       policy=policy, github=manager.verifier.github, kind='manager')
        publish_request(policy,candidate,base64.b64decode(request['envelope'],validate=True),request)
        path = STATE / ('bundle-' + candidate.manifest_hash.removeprefix('sha256:') + '.zip')
        protected(path)
        return manager.run(path.read_bytes(), candidate, base64.b64decode(request['envelope'], validate=True))
    require(False, 'cd-bridge-operation-forbidden')


def main(role):
    raw = sys.stdin.buffer.readline(HEADER_LIMIT + 1)
    require(len(raw) <= HEADER_LIMIT and raw.endswith(b'\n'), 'cd-bridge-header-invalid')
    request = closed_json(raw[:-1], HEADER_LIMIT)
    from scripts.cd.diagnostics import blocked
    try:
        result = handle(role, request, sys.stdin.buffer)
    except Exception as error:
        result = blocked(error, 'cd-trusted-intake-failed')
    print(canonical(result).decode())
    return 0 if result.get('status') not in ('blocked', 'recovery-required', 'rolled-back') else 2


def recover_main():
    """Administrative root channel + fresh operator SSHSIG; not an SSH deploy role."""
    path=STATE/'operator-recovery.json'
    protected(path)
    request=closed_json(path.read_bytes(),HEADER_LIMIT)
    require(set(request)=={'nonce','envelope'},'cd-recovery-fields-invalid')
    installed=installation()
    policy=installed_policy(installed)
    result=updater(installed,policy).recover(request['nonce'],base64.b64decode(request['envelope'],validate=True))
    print(canonical(result).decode())
    return 0
