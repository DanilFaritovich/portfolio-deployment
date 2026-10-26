"""Signed recovery with real SQLite/locks; no host or Docker access."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.cd import bootstrap_recovery as recovery
from scripts.cd.signatures import sign
from scripts.deployment_journal import Intent, Journal
from scripts.docker_adapter import DockerError
from scripts.production_integration.authorization import ApprovalClaims
from scripts.readiness import ReadinessEvidence
from test_bootstrap_containment import world as contained_world, no_network
from test_cd import identities


@pytest.fixture
def world(contained_world):
    w = contained_world
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        journal.transition(w.run, 'gateway', 'verifying', 'mutation-completed')
    calls = []
    def observe(candidate, lease):
        assert lease.fd >= 0 and candidate == w.candidate
        calls.append('read-only-probe')
        return {'runtime': candidate.runtime_hash, 'resources': ['network', 'private', 'caddy', 'active']}, \
            ReadinessEvidence('verified', 'readiness-verified', 2, 2)
    old = w.port
    w.port = recovery.BootstrapRecovery(installation=old.installation, policy=old.policy, lock=w.lock,
        observe=observe, inventories=old.inventories, keys=old.keys, directory=w.directory, state=w.state)
    w.calls = calls
    return w


def approved(w, decision=None, *, role='operator', namespace=recovery.NAMESPACE):
    decision = w.port.prepare()['decision'] if decision is None else decision
    return sign(decision, principal=role, key=w.identities[role][0], namespace=namespace)


def test_prepare_is_read_only_and_completion_records_real_readiness(world):
    w = world
    original = recovery.journal_bytes(w.directory)
    decision = w.port.prepare()['decision']
    assert recovery.journal_bytes(w.directory) == original and not list(w.state.iterdir())
    envelope = approved(w, decision)
    result = w.port.execute(envelope)
    assert result['status'] == 'succeeded' and result['lastGoodCreated']
    assert result['executorStarted'] is result['bootstrapReplayed'] is False
    target = w.state / (w.port.audit_prefix + decision['id'])
    assert (target / 'original.sqlite3').read_bytes() == original
    assert (target / 'approval.json').read_bytes() == envelope
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        assert not journal.incomplete() and not journal.authorization_recovery()
        assert journal.production_bootstrap(w.candidate.host, w.candidate.daemon)
        assert journal.last_good('manager-bootstrap', 'gateway')['consecutive'] == 2
        assert [e[0] for e in journal.db.execute('SELECT state FROM events ORDER BY sequence')] == [
            'prepared', 'applying', 'verifying', 'succeeded']
    committed = recovery.journal_bytes(w.directory)
    assert not w.port.execute(envelope)['lastGoodCreated']
    assert recovery.journal_bytes(w.directory) == committed


def test_previous_containment_is_preserved(world):
    w = world
    # Build a genuine contained prior attempt using its existing primitive.
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        journal.db.execute("DELETE FROM events WHERE state='verifying'")
        event = journal.db.execute('SELECT at FROM events ORDER BY sequence DESC LIMIT 1').fetchone()[0]
        journal.db.execute("UPDATE attempts SET state='applying',reason='mutation-started',updated=?", (event,))
        journal.contain_empty_bootstrap(w.run, w.candidate.identity)
        prior = journal.rows()
        run = '9'*32
        w.candidate = replace(w.candidate, plan_hash='sha256:'+'0'*64)
        journal.consume_authorization(ApprovalClaims(w.candidate.identity, 'operator', 1, 301, run), w.candidate)
        journal.prepare(run, 'manager-bootstrap', w.candidate.manager_sha,
                        (Intent('gateway', None, w.candidate.image, w.candidate.runtime_hash.removeprefix('sha256:')),))
        journal.transition(run, 'gateway', 'applying', 'mutation-started')
        journal.transition(run, 'gateway', 'verifying', 'mutation-completed')
        journal.fail_run(run)
        journal.finish_authorization(run, 'recovery-required')
    w.run = run
    w.port.execute(approved(w))
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        assert [r for r in journal.rows() if r['run'] != run] == prior
        assert journal.production_bootstrap(w.candidate.host, w.candidate.daemon)


@pytest.mark.parametrize('field,value', [('run','9'*32), ('candidate','sha256:'+'0'*64),
    ('host','0'*32), ('daemon','other'), ('manager_sha','0'*40), ('policy_id','sha256:'+'0'*64),
    ('journal_sha256','0'*64), ('capsule_sha256','0'*64), ('inventory_sha256','0'*64),
    ('observation_sha256','sha256:'+'0'*64), ('expires',0), ('version',True)])
def test_changed_or_expired_decision_never_mutates(world, field, value):
    w = world
    decision = w.port.prepare()['decision']
    decision[field] = value
    original = recovery.journal_bytes(w.directory)
    with pytest.raises((DockerError, KeyError)):
        w.port.execute(approved(w, decision))
    assert recovery.journal_bytes(w.directory) == original and not list(w.state.iterdir())


@pytest.mark.parametrize('role,namespace', [('automation', recovery.NAMESPACE),
    ('observer', recovery.NAMESPACE), ('operator', 'portfolio-bootstrap-containment-v1')])
def test_delegated_roles_and_other_signature_namespaces_cannot_recover(world, role, namespace):
    w = world
    original = recovery.journal_bytes(w.directory)
    with pytest.raises(DockerError):
        w.port.execute(approved(w, role=role, namespace=namespace))
    assert recovery.journal_bytes(w.directory) == original and not list(w.state.iterdir())


@pytest.mark.parametrize('failure', ['probe', 'unverified', 'changed', 'changed-after-audit', 'expired-after-probe', 'commit'])
def test_failure_preserves_journal_and_can_retry_only_exact_decision(world, monkeypatch, failure):
    w = world
    decision = w.port.prepare()['decision']
    envelope = approved(w, decision)
    original = recovery.journal_bytes(w.directory)
    observe = w.port.observe
    if failure == 'probe':
        def fail(*args): raise RuntimeError('probe failed')
        monkeypatch.setattr(w.port, 'observe', fail)
    elif failure == 'unverified':
        monkeypatch.setattr(w.port, 'observe', lambda *args: ({}, ReadinessEvidence('unknown', 'runtime-unverified', 0, 0)))
    elif failure == 'changed':
        monkeypatch.setattr(w.port, 'observe', lambda *args: ({'changed': True}, observe(*args)[1]))
    elif failure == 'changed-after-audit':
        calls = []
        def change(*args):
            calls.append(1)
            result = observe(*args)
            return result if len(calls) == 1 else ({'changed': True}, result[1])
        monkeypatch.setattr(w.port, 'observe', change)
    elif failure == 'expired-after-probe':
        def expire(*args):
            result = observe(*args)
            w.port.clock = lambda: decision['expires']
            return result
        monkeypatch.setattr(w.port, 'observe', expire)
    else:
        event = Journal._event
        def fail_event(journal, *args):
            event(journal, *args)
            raise RuntimeError('interrupted transaction')
        monkeypatch.setattr(Journal, '_event', fail_event)
    with pytest.raises((DockerError, RuntimeError)):
        w.port.execute(envelope)
    assert recovery.journal_bytes(w.directory) == original
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        assert journal.last_good('manager-bootstrap', 'gateway') is None
        assert len(journal.incomplete()) == len(journal.authorization_recovery()) == 1
    if failure == 'commit':
        monkeypatch.setattr(Journal, '_event', event)
        assert w.port.execute(envelope)['lastGoodCreated']


@pytest.mark.parametrize('failure', ['applying', 'failed', 'prior-lkg', 'different-installation'])
def test_other_recovery_states_are_not_adopted(world, failure):
    w = world
    if failure == 'different-installation':
        w.port.installation.manager_sha = '0'*40
    else:
        with w.lock() as lease, Journal(w.directory, lease) as journal:
            if failure == 'applying':
                journal.db.execute("DELETE FROM events WHERE state='verifying'")
                stamp = journal.db.execute('SELECT at FROM events ORDER BY sequence DESC LIMIT 1').fetchone()[0]
                journal.db.execute("UPDATE attempts SET state='applying',reason='mutation-started',updated=?", (stamp,))
            elif failure == 'failed':
                journal.transition(w.run, 'gateway', 'failed', 'runtime-unverified')
            else:
                journal.db.execute('INSERT INTO good VALUES (?,?,?)', ('manager-bootstrap','gateway',w.run))
    original = recovery.journal_bytes(w.directory)
    with pytest.raises((DockerError, RuntimeError)):
        w.port.prepare()
    assert recovery.journal_bytes(w.directory) == original


def test_completed_reply_requires_original_signed_evidence(world):
    w = world
    decision = w.port.prepare()['decision']
    envelope = approved(w, decision)
    w.port.execute(envelope)
    original = recovery.journal_bytes(w.directory)
    target = w.state / (w.port.audit_prefix + decision['id'])
    (target / 'approval.json').write_bytes(b'tampered')
    with pytest.raises(DockerError): w.port.execute(envelope)
    assert recovery.journal_bytes(w.directory) == original


def test_operator_main_uses_fixed_read_only_composition(monkeypatch, capsys):
    import io
    import sys
    from scripts.cd import administrative, application, bridge
    from scripts.production_integration import fixed_bindings, locking, operator_approval
    installed, policy, lease, fixed = object(), object(), object(), object()
    candidate, runtime = object(), object()
    calls = []
    monkeypatch.setattr(recovery.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(bridge, 'installation', lambda: installed)
    monkeypatch.setattr(application, 'installed_policy', lambda value: policy)
    monkeypatch.setattr(administrative, 'private', lambda *args: b'reviewed fixture')
    monkeypatch.setattr(operator_approval, 'trust_keys', lambda raw: {'operator': 'fixture'})
    monkeypatch.setitem(sys.modules, 'cd_bindings', SimpleNamespace(POLICY=fixed))
    monkeypatch.setattr(locking, 'require_production_lease', lambda value: calls.append(('lease', value)))
    def build(installation, wiring):
        assert installation is installed and wiring is fixed
        resources = SimpleNamespace(verify_existing=lambda c, r: calls.append(('probe', c, r)))
        return SimpleNamespace(bootstrap=SimpleNamespace(port=SimpleNamespace(
            caddy=runtime, attestor=SimpleNamespace(resources=resources))))
    monkeypatch.setattr(fixed_bindings, 'build_fixed', build)
    class Operation:
        def __init__(self, **kwargs):
            assert kwargs['installation'] is installed and kwargs['policy'] is policy
            self.observe = kwargs['observe']
        def prepare(self):
            self.observe(candidate, lease)
            return {'status': 'prepared', 'executorStarted': False}
    monkeypatch.setattr(recovery, 'BootstrapRecovery', Operation)
    monkeypatch.setattr(recovery.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"op":"prepare"}')))
    assert recovery.main() == 0
    assert calls == [('lease', lease), ('probe', candidate, runtime)]
    assert 'prepared' in capsys.readouterr().out


@pytest.mark.parametrize('role', ['automation', 'observer'])
def test_forced_ssh_roles_cannot_invoke_bootstrap_recovery(monkeypatch, role):
    from scripts.cd import bridge
    monkeypatch.setattr(bridge, 'installation', lambda: object())
    monkeypatch.setattr(bridge, 'installed_policy', lambda *args: object())
    monkeypatch.setattr(bridge, 'updater', lambda *args: SimpleNamespace(fenced=lambda: None))
    with pytest.raises(DockerError, match='scope-forbidden|operation-forbidden'):
        bridge.handle(role, {'op': 'operator-bootstrap-verification-recovery'}, None)
