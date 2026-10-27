"""Rollout budget boundaries through real preflight, signature, executor and LKG.

Only external I/O is controlled. Virtual costs include preparation, post-barrier
authority, ACTIVE reads, closing seals, host reads and final Engine corroboration.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import hashlib
import subprocess

import pytest

from scripts.deployment_journal import Journal
from scripts.docker_adapter import DockerError
from scripts.production_integration.observation_window import ROLLOUT_OBSERVATION_SECONDS, observation_window
from test_trusted_bindings import integrated, clocked_joint_rollout
from test_operator_approval import signer, verifier, local_crypto_only
from test_production_execution import setup
from test_production_contracts import registration
from test_production_integration import router


@pytest.fixture
def rollout(clocked_joint_rollout, monkeypatch):
    s, timing = clocked_joint_rollout
    transport = s.ports['gateway'].port.transport
    for port in (s.preflight, s.host, s.provider, s.provider.reader, transport):
        port.max_age = ROLLOUT_OBSERVATION_SECONDS
    timing.host_seconds, timing.state_seconds = 11.36, 1
    timing.active_seconds, timing.seal_seconds = 7.36, 3
    inspect = transport.runtime.inspect
    def runtime(channel):
        timing.now += timing.active_seconds / 2
        return inspect(channel)
    @contextmanager
    def observation():
        timing.now += 6  # Complete opening seal, before the mutable clocks.
        yield
        timing.now += timing.seal_seconds  # Complete closing seal, no retimestamp.
    def authority(policy, identity):
        timing.now += 8  # Post-barrier full authority scan, before live reads.
        return policy
    monkeypatch.setattr(transport.runtime, 'inspect', runtime)
    monkeypatch.setattr(transport.runtime, 'observation', observation, raising=False)
    monkeypatch.setattr(s.authority, 'verify', authority)
    s.executor.clock = lambda: 100 + timing.now
    return s, timing


def test_production_costs_complete_signed_rollout_and_reject_replay(rollout, signer):
    s, timing = rollout
    candidate = s.prepare()
    assert timing.now > 35
    now = int(s.executor.clock())
    envelope = signer.sign(dict(version=1, candidate=candidate.identity, operator='operator',
        nonce='1'*32, not_before=now-1, expires=now+899))
    result = s.executor.run(candidate, envelope)
    assert result.status == 'succeeded', result
    assert s.world.calls == [('up', 'portfolio-project-router', 'web'), ('gateway',)]
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        good = journal.last_good('project-router', 'web')
        assert good['run'] == '1'*32 and good['readiness'] == 'verified'
    calls = list(s.world.calls)
    assert s.executor.run(candidate, envelope).status == 'blocked'
    assert s.world.calls == calls


def test_operator_approval_expiring_during_revalidation_never_mutates(rollout, signer):
    s, _ = rollout
    candidate = s.prepare()
    now = int(s.executor.clock())
    envelope = signer.sign(dict(version=1, candidate=candidate.identity, operator='operator',
        nonce='2'*32, not_before=now-1, expires=now+1))
    result = s.executor.run(candidate, envelope)
    assert result.status == 'blocked'
    assert result.reasons == ('production-approval-stale-or-binding-invalid',)
    assert not s.world.calls


@pytest.mark.parametrize('read,closing,valid', [(7.36, 3, True), (7, 8, True), (7, 8.01, False)])
def test_runtime_revalidation_bounds_new_reads_without_renewing_old_proof(integrated, monkeypatch, read, closing, valid):
    s = integrated
    port = s.ports['gateway'].port.transport
    tick = [0]
    port.clock, port.max_age = lambda:tick[0], ROLLOUT_OBSERVATION_SECONDS
    sample = port.read_active()
    old_proof = port.last
    tick[0] = 100  # Prior validation/reload has outlived the old source proof.
    inspect = port.runtime.inspect
    def current(channel):
        tick[0] += read
        return inspect(channel)
    @contextmanager
    def sealed():
        tick[0] += 50  # Opening seal preparation cannot mint/renew source proof.
        yield
        tick[0] += closing
    monkeypatch.setattr(port.runtime, 'inspect', current)
    monkeypatch.setattr(port.runtime, 'observation', sealed, raising=False)
    if valid:
        port.verify_current(sample)
    else:
        with pytest.raises(DockerError, match='expired-or-changed'):
            port.verify_current(sample)
    assert port.last is old_proof and port.last[2] == 0
    with pytest.raises(DockerError):
        port.verify(port, SimpleNamespace(image_id=sample.image_config_id), sample.version, sample)


def test_runtime_revalidation_rejects_changed_baseline(integrated, monkeypatch):
    from dataclasses import replace
    port = integrated.ports['gateway'].port.transport
    sample = port.read_active()
    inspect = port.runtime.inspect
    monkeypatch.setattr(port.runtime, 'inspect', lambda channel:replace(inspect(channel), channel_identity=('foreign',)))
    with pytest.raises(DockerError, match='expired-or-changed'):
        port.verify_current(sample)


@pytest.mark.parametrize('failure', [None, 'reload', 'witness'])
def test_full_gateway_public_witness_and_lkg_with_slow_preparation(rollout, signer, monkeypatch, tmp_path, failure):
    from functools import partial
    from scripts.deployment import owns_host_lock
    from scripts.production_integration import gateway_write as gateway, public_postconditions as public, public_witness as witness
    from scripts.reconciliation import encode
    s, timing = rollout
    disk = [gateway.content(())]
    events = []
    read_port = s.ports['gateway'].port
    def seal():
        timing.now += 2
    installation = SimpleNamespace(verify=seal, files=((gateway.BINARY, 'fixture'), (witness.BINARY, 'fixture')))
    monkeypatch.setattr(gateway, 'ProductionGateway', partial(gateway.ProductionGateway,
        lease_verifier=lambda lease: owns_host_lock(lease)))
    s.executor.gateway = gateway.GatewayExecutionFactory(installation, read_port, s.engine)
    monkeypatch.setattr(gateway, 'protected', lambda *args:None)
    monkeypatch.setattr(gateway, 'validate_storage_directory', lambda *args, **kwargs:None)
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p:disk[0].encode()
        if str(p) == '/srv/portfolio/caddy-config/Caddyfile' else original(p))
    def publish(directory, name, raw):
        events.append(('durable', directory))
        if directory == '/srv/portfolio/caddy-config':
            disk[0] = raw.decode()
    monkeypatch.setattr(gateway, 'durable_replace', publish)
    def command(argv, **kwargs):
        if argv[1] == 'reload':
            assert kwargs['timeout'] == 20 and Path(argv[5]).read_bytes() == disk[0].encode()
            timing.now += 19  # Valid reload can outlive a 15-second old baseline.
            if failure == 'reload':
                raise DockerError('synthetic-reload-lost-reply', True)
            s.world.route = True
            s.world.calls.append(('gateway',))
            events.append(('reload',))
        else:
            assert argv[1] in ('adapt', 'validate') and kwargs['timeout'] == 15
            timing.now += 8
        return b''
    monkeypatch.setattr(gateway, 'run_bounded', command)
    ca = tmp_path / 'ca.pem'
    ca.write_bytes(b'test-only-ca')
    body = b'<!doctype html><title>ProjectRouter</title>'
    raw = b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
    policy = public.PublicPolicy(s.engine.identity(), ('8.8.8.8',), (), '1.1.1.1', str(ca),
        hashlib.sha256(ca.read_bytes()).hexdigest(), hashlib.sha256(body).hexdigest())
    observer_key = tmp_path / 'test-observer'
    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(observer_key)], check=True)
    observer_public = ' '.join(observer_key.with_suffix('.pub').read_text().split()[:2])
    receipt = tmp_path / 'test-observer-receipt.json'
    monkeypatch.setattr(witness, 'RECEIPT', receipt)
    monkeypatch.setattr(witness, 'protected', lambda *args:None)
    signed_witness = witness.SignedPublicWitness(installation, observer_public, clock=s.executor.clock)
    class Network:
        def resolve(self, resolver, kind, deadline):
            assert s.world.route
            timing.now += 0.1
            return policy.ipv4 if kind == 1 else ()
        def https(self, address, ca, deadline):
            timing.now += 0.2
            return raw
    class Observer:
        def verify(self, subject, *, timeout):
            assert s.world.route and 0 < timeout <= public.TOTAL_TIMEOUT
            timing.now += 16 if failure == 'witness' else 0.5
            now = int(s.executor.clock())
            payload = dict(subject=subject, observed_at=now, expires=now+30)
            signature = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(observer_key), '-n', witness.NAMESPACE],
                input=encode(payload).encode(), capture_output=True, check=True).stdout.decode()
            receipt.write_text(encode(dict(payload=payload, signature=signature)))
            result = signed_witness.verify(subject, timeout=timeout)
            events.append(('witness',))
            return result
    s.ports['gateway'].postconditions = public.PublicPostconditions(policy, external=Observer(),
        network=Network(), clock=lambda:timing.now)
    candidate = s.prepare()
    now = int(s.executor.clock())
    envelope = signer.sign(dict(version=1, candidate=candidate.identity, operator='operator',
        nonce='1'*32, not_before=now-1, expires=now+899))
    result = s.executor.run(candidate, envelope)
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        good = journal.last_good('project-router', 'web')
        assert (good is not None) == (failure is None), result
    if failure is None:
        assert result.status == 'succeeded', result
        assert ('witness',) in events and ('reload',) in events
        assert events.index(('durable', '/srv/portfolio/caddy-lkg')) < events.index(('reload',))
    else:
        assert result.status != 'succeeded'
        calls = list(s.world.calls)
        assert s.executor.run(candidate, envelope).status == 'blocked'
        assert s.world.calls == calls


@pytest.mark.parametrize('slow', ['host', 'active', 'seal', 'engine', 'plan'])
def test_every_production_window_rejects_over_budget_before_mutation(rollout, monkeypatch, slow):
    s, timing = rollout
    if slow == 'plan':
        from scripts.production_integration import candidate
        build = candidate.build_gateway_candidate
        def late(*args, **kwargs):
            result = build(*args, **kwargs)
            timing.now += 16
            return result
        monkeypatch.setattr(candidate, 'build_gateway_candidate', late)
    else:
        name = {'host':'host_seconds', 'active':'active_seconds', 'seal':'seal_seconds', 'engine':'state_seconds'}[slow]
        setattr(timing, name, 16)
    with pytest.raises(DockerError):
        s.prepare()
    assert not s.world.calls and s.provider.sample is None


@pytest.mark.parametrize('value', [True, False, 0, -1, 31, float('inf'), float('nan'), '15', None])
def test_observation_budget_rejects_unbounded_or_ambiguous_values(value):
    with pytest.raises(DockerError, match='production-observation-window-invalid'):
        observation_window(value)
