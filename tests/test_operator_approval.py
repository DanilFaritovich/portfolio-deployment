"""Real local SSHSIG keys only; network and every non-keygen process forbidden."""
from dataclasses import replace
import hashlib
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from scripts.docker_adapter import DockerError
from scripts.production_integration import operator_approval as approval
from scripts.production_integration.authorization import review_approval
from scripts.production_integration.installation import SealedInstallation
from test_production_preparation import candidate
from test_production_execution import setup
from test_production_integration import router
from test_production_contracts import registration

REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def local_crypto_only(monkeypatch):
    def process(argv, *args, **kwargs):
        assert argv[0] == '/usr/bin/ssh-keygen', 'non-crypto subprocess forbidden'
        return REAL_POPEN(argv, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('network forbidden in offline approval tests')
    monkeypatch.setattr(subprocess, 'Popen', process)
    monkeypatch.setattr(socket, 'socket', forbidden)


@pytest.fixture
def signer(tmp_path):
    key = tmp_path / 'operator'
    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    public = ' '.join(key.with_suffix('.pub').read_text().split()[:2])
    def sign(payload, namespace=approval.NAMESPACE):
        raw = approval.canonical_payload(payload)
        result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', namespace],
                                input=raw, capture_output=True, check=True)
        return approval.canonical({'payload': payload, 'signature': result.stdout.decode()})
    return SimpleNamespace(public=public, sign=sign)


@pytest.fixture
def payload():
    return dict(version=1, candidate=candidate().identity, operator='operator', nonce='1'*32,
                not_before=90, expires=150)


@pytest.fixture
def verifier(signer, tmp_path, monkeypatch):
    trust = tmp_path / 'operator-keys.json'
    trust.write_bytes(approval.canonical({'version': 1, 'operators': {'operator': signer.public}}))
    installation = SealedInstallation('a'*40, tuple((str(p), hashlib.sha256(p.read_bytes()).hexdigest())
                                                 for p in (approval.BINARY, trust)))
    # Only filesystem privilege/context checks are substituted; crypto is real.
    monkeypatch.setattr(SealedInstallation, 'verify', lambda self: None)
    monkeypatch.setattr(approval, 'protected', lambda path: None)
    monkeypatch.setattr(approval, 'TRUST', trust)
    return approval.SSHSIGVerifier(installation)


def test_real_signature_and_review(verifier, signer, payload):
    envelope = signer.sign(payload)
    claims = review_approval(candidate(), envelope, verifier=verifier, now=100)
    assert claims.candidate == candidate().identity and claims.operator == 'operator'
    for now in (89, 150, 151):
        with pytest.raises(DockerError):
            review_approval(candidate(), envelope, verifier=verifier, now=now)
    with pytest.raises(DockerError):
        review_approval(replace(candidate(), host='f'*32), envelope, verifier=verifier, now=100)
    with pytest.raises(DockerError):
        review_approval(candidate(), envelope, now=100)


@pytest.mark.parametrize('field,value', [('candidate', 'sha256:'+'f'*64), ('operator', 'other'),
                                        ('nonce', '2'*32), ('not_before', 91), ('expires', 151)])
def test_unsigned_claim_changes_fail(verifier, signer, payload, field, value):
    document = json.loads(signer.sign(payload))
    document['payload'][field] = value
    with pytest.raises(DockerError):
        verifier.authenticate(approval.canonical(document))


@pytest.mark.parametrize('case', ['key', 'principal', 'namespace', 'corrupt', 'missing', 'timeout', 'failure'])
def test_crypto_failures(verifier, signer, payload, case, tmp_path, monkeypatch):
    envelope = signer.sign(payload)
    if case == 'key':
        other = tmp_path / 'other'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(other)], check=True)
        public = ' '.join(other.with_suffix('.pub').read_text().split()[:2])
        monkeypatch.setattr(approval, 'trust_keys', lambda raw: {'operator': public})
    elif case == 'principal':
        envelope = signer.sign({**payload, 'operator': 'unapproved'})
    elif case == 'namespace':
        envelope = signer.sign(payload, 'wrong-namespace')
    elif case == 'corrupt':
        document = json.loads(envelope)
        armor = document['signature'].splitlines()
        import base64
        wire = bytearray(base64.b64decode(''.join(armor[1:-1])))
        wire[-1] ^= 1
        document['signature'] = armor[0]+'\n'+base64.b64encode(wire).decode()+'\n'+armor[-1]+'\n'
        envelope = approval.canonical(document)
    else:
        def broken(*args, **kwargs):
            if case == 'missing': raise FileNotFoundError()
            if case == 'timeout': raise subprocess.TimeoutExpired('ssh-keygen', 5)
            return SimpleNamespace(returncode=1)
        monkeypatch.setattr(subprocess, 'run', broken)
    with pytest.raises(DockerError):
        verifier.authenticate(envelope)


@pytest.mark.parametrize('raw', [b'{', b'{}', b'[]', b'null', b'\xff', b'x'*8193,
    b'{"payload":{},"payload":{},"signature":""}', b'['*2000+b']'*2000])
def test_malformed_json(verifier, raw):
    with pytest.raises(DockerError): verifier.authenticate(raw)


@pytest.mark.parametrize('change', [dict(version=2), dict(version=True), dict(extra=True),
    dict(not_before=90.0), dict(not_before=True), dict(expires=4000), dict(expires=-1),
    dict(expires='150'), dict(nonce='bad'), dict(operator='*'), dict(candidate='bad')])
def test_closed_payload(verifier, signer, payload, change):
    document = json.loads(signer.sign(payload))
    document['payload'].update(change)
    with pytest.raises(DockerError): verifier.authenticate(approval.canonical(document))


@pytest.mark.parametrize('case', ['signature-limit', 'extra', 'duplicate', 'whitespace', 'exponent'])
def test_closed_envelope(verifier, signer, payload, case):
    raw = signer.sign(payload)
    doc = json.loads(raw)
    if case == 'signature-limit': doc['signature'] = 'x'*2049
    elif case == 'extra': doc['namespace'] = approval.NAMESPACE
    elif case == 'duplicate': raw = raw.replace(b'"version":1', b'"version":1,"version":1')
    elif case == 'whitespace': raw += b'\n'
    elif case == 'exponent': raw = raw.replace(b'"expires":150', b'"expires":15e1')
    if case in ('extra', 'signature-limit'): raw = approval.canonical(doc)
    with pytest.raises(DockerError): verifier.authenticate(raw)


@pytest.mark.parametrize('operators', [{}, {'*': 'ssh-ed25519 invalid'}, {'operator': 'ssh-rsa invalid'}])
def test_unconfigured_or_unsafe_keys(operators):
    with pytest.raises(DockerError):
        approval.trust_keys(approval.canonical({'version': 1, 'operators': operators}))


def test_missing_pins_and_changed_trust(verifier, signer, payload):
    envelope = signer.sign(payload)
    installation = verifier.installation
    for pins in ((), (installation.files[0],), (installation.files[1],)):
        verifier.installation = replace(installation, files=pins)
        with pytest.raises(DockerError): verifier.authenticate(envelope)
    verifier.installation = installation
    approval.TRUST.write_bytes(b'{}')
    with pytest.raises(DockerError): verifier.authenticate(envelope)


def test_executor_real_crypto_reauth_and_replay(setup, verifier, signer, payload):
    s = setup
    c = s.preflight.prepare('first-project-router-rollout').candidate
    envelope = signer.sign({**payload, 'candidate': c.identity})
    calls = []
    class CountingVerifier:
        def authenticate(self, envelope):
            calls.append(True)
            return verifier.authenticate(envelope)
    s.executor.verifier = CountingVerifier()
    assert s.executor.run(c, envelope).status == 'succeeded'
    assert len(calls) == 2
    s.world.present, s.world.route = False, False
    assert s.executor.run(c, envelope).status == 'blocked'
    fresh_nonce = signer.sign({**payload, 'candidate': c.identity, 'nonce': '2'*32})
    assert s.executor.run(c, fresh_nonce).status == 'blocked'
    assert len(s.world.calls) == 2


def test_candidate_changed_before_mutation(setup, verifier, signer, payload):
    s = setup
    c = s.preflight.prepare('first-project-router-rollout').candidate
    envelope = signer.sign({**payload, 'candidate': c.identity})
    review_approval(c, envelope, verifier=verifier, now=100)
    s.executor.verifier = verifier
    s.preflight.content_reader = lambda: (s.world.desired, b'changed', b'policy')
    assert s.executor.run(c, envelope).status == 'blocked'
    assert not s.world.calls


def test_offline_cli(verifier, signer, payload, tmp_path, monkeypatch, capsys):
    from scripts import verify_operator_approval as cli
    path = tmp_path / 'envelope'
    path.write_bytes(signer.sign(payload))
    monkeypatch.setattr(cli.time, 'time', lambda: 100)
    pins = dict(verifier.installation.files)
    args = ['--envelope', str(path), '--trust', str(approval.TRUST), '--trust-sha256', pins[str(approval.TRUST)],
            '--ssh-keygen-sha256', pins[str(approval.BINARY)]]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)['productionAuthorized'] is False
    monkeypatch.setattr(cli.time, 'time', lambda: 150)
    assert cli.main(args) == 2
    args[-1] = '0'*64
    assert cli.main(args) == 2


@pytest.mark.parametrize('case', ['revoked', 'expired'])
def test_reauthentication_fails_before_consumption(setup, verifier, signer, payload, case):
    s = setup
    c = s.preflight.prepare('first-project-router-rollout').candidate
    envelope = signer.sign({**payload, 'candidate': c.identity})
    s.executor.verifier = verifier
    original = s.executor.release_factory
    def release(candidate):
        proof = original(candidate)
        def verify(request):
            result = proof.verify(request)
            if case == 'revoked': approval.TRUST.write_bytes(b'{}')
            else: s.executor.clock = lambda: 150
            return result
        return SimpleNamespace(verify=verify)
    s.executor.release_factory = release
    assert s.executor.run(c, envelope).status == 'blocked'
    assert not s.world.calls
    from scripts.deployment_journal import Journal
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.db.execute('SELECT 1 FROM authorizations WHERE nonce=?', (payload['nonce'],)).fetchone() is None


@pytest.mark.parametrize('field,value', [('version', 2), ('version', True), ('extra', []), ('operators', {})])
def test_closed_trust_schema(field, value):
    doc = {'version': 1, 'operators': {}}
    doc[field] = value
    with pytest.raises(DockerError): approval.trust_keys(approval.canonical(doc))


def test_unapproved_wire_hash(verifier, signer, payload, tmp_path):
    # An otherwise genuine OpenSSH signature with a non-policy hash is rejected.
    key = tmp_path / 'operator'
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-O', 'hashalg=sha256',
                             '-f', str(key), '-n', approval.NAMESPACE],
                            input=approval.canonical_payload(payload), capture_output=True, check=True)
    with pytest.raises(DockerError):
        verifier.authenticate(approval.canonical({'payload': payload, 'signature': result.stdout.decode()}))


def test_sealed_installation_failure_precedes_authentication(verifier, signer, payload, monkeypatch):
    def blocked(self): raise DockerError('production-installation-content-changed')
    monkeypatch.setattr(SealedInstallation, 'verify', blocked)
    with pytest.raises(DockerError, match='production-installation-content-changed'):
        verifier.authenticate(signer.sign(payload))
