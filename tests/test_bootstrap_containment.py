"""Real signatures, lock and SQLite; empty-host observations are offline doubles."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from scripts.cd import bootstrap_containment as containment
from scripts.cd.signatures import sign
from scripts.deployment import host_lock
from scripts.deployment_journal import Intent, Journal, JournalError
from scripts.docker_adapter import DockerError
from scripts.production_integration.authorization import OperationCandidate, ApprovalClaims
from test_cd import identities


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('live network attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)


@pytest.fixture
def world(tmp_path, identities):
    run = '1'*32
    candidate = OperationCandidate('2'*32, 'daemon', 'a'*40, 'b'*40,
        'ghcr.io/danilfaritovich/project-router@sha256:' + 'c'*64,
        *('sha256:' + c*64 for c in 'defabcde'), 'first-bootstrap')
    directory = tmp_path / 'journal'
    state = tmp_path / 'cd-state'
    state.mkdir(mode=0o700)
    @contextmanager
    def lock():
        with host_lock(str(tmp_path / 'locks')) as lease:
            yield lease
    claims = ApprovalClaims(candidate.identity, 'operator', 1, 301, run)
    with lock() as lease, Journal(directory, lease) as journal:
        journal.consume_authorization(claims, candidate)
        journal.prepare(run, 'manager-bootstrap', candidate.manager_sha,
                        (Intent('gateway', None, candidate.image, candidate.runtime_hash.removeprefix('sha256:')),))
        journal.transition(run, 'gateway', 'applying', 'mutation-started')
        journal.fail_run(run)
        journal.finish_authorization(run, 'recovery-required')
    observations = []
    def observe(lease):
        assert lease.fd >= 0
        observations.append('complete-empty-host')
    policy = SimpleNamespace(document={'host': candidate.host, 'daemon': candidate.daemon}, identity='sha256:'+'f'*64)
    policy.current = lambda: policy
    installation = SimpleNamespace(manager_sha=candidate.manager_sha, verify=lambda: None)
    port = containment.BootstrapContainment(installation=installation, policy=policy, lock=lock,
        observe_empty=observe, inventories=lambda: {'inventory_sha256': 'a'*64, 'capsule_sha256': 'b'*64},
        keys={'operator': identities['operator'][1]}, directory=directory, state=state)
    return SimpleNamespace(port=port, directory=directory, state=state, lock=lock,
                           run=run, candidate=candidate, identities=identities, observations=observations)


def approved(world, decision=None, role='operator', namespace=containment.NAMESPACE):
    if decision is None:
        decision = world.port.prepare()['decision']
    return sign(decision, principal=role, key=world.identities[role][0], namespace=namespace)


def test_prepare_is_read_only_and_closure_retains_original_history(world):
    w = world
    before = containment.journal_bytes(w.directory)
    decision = w.port.prepare()['decision']
    assert containment.journal_bytes(w.directory) == before and list(w.state.iterdir()) == []
    envelope = approved(w, decision)
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        events = [tuple(r) for r in journal.db.execute('select * from events order by sequence')]
    result = w.port.execute(envelope)
    assert result == {'status': 'contained', 'run': w.run, 'candidate': w.candidate.identity,
                      'executorStarted': False, 'bootstrapReplayed': False, 'lastGoodCreated': False}
    audit = w.state / ('bootstrap-containment-' + decision['id'])
    assert (audit / 'original.sqlite3').read_bytes() == before
    assert (audit / 'approval.json').read_bytes() == envelope
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        assert journal.incomplete() == [] and journal.authorization_recovery() == ()
        assert journal.last_good('manager-bootstrap', 'gateway') is None
        assert journal.production_bootstrap(w.candidate.host, w.candidate.daemon) is False
        assert [tuple(r) for r in journal.db.execute('select * from events order by sequence limit 2')] == events
        assert journal.db.execute('select state from authorizations').fetchone()[0] == 'blocked'
        assert journal.rows()[0]['run_status'] == 'contained'
    closed = containment.journal_bytes(w.directory)
    assert w.port.execute(envelope) == result
    assert containment.journal_bytes(w.directory) == closed


@pytest.mark.parametrize('field', ['host', 'daemon', 'manager_sha', 'inventory_sha256', 'capsule_sha256',
                                  'policy_id', 'journal_sha256', 'run', 'candidate', 'expired', 'wide', 'extra'])
def test_exact_signature_decision_rejects_drift_before_writes(world, field):
    w = world
    decision = w.port.prepare()['decision']
    if field == 'expired': decision['expires'] = decision['not_before'] + 1
    elif field == 'wide': decision['expires'] = decision['not_before'] + 301
    elif field == 'extra': decision['reset'] = True
    else: decision[field] = '0' * len(decision[field])
    before = containment.journal_bytes(w.directory)
    with pytest.raises((DockerError, JournalError)):
        w.port.execute(approved(w, decision))
    assert containment.journal_bytes(w.directory) == before and list(w.state.iterdir()) == []


@pytest.mark.parametrize('role,namespace', [('automation', containment.NAMESPACE),
    ('observer', containment.NAMESPACE), ('operator', 'portfolio-deployment-operation-v1')])
def test_delegated_or_wrong_namespace_signatures_cannot_contain(world, role, namespace):
    with pytest.raises(DockerError):
        world.port.execute(approved(world, role=role, namespace=namespace))
    assert list(world.state.iterdir()) == []


@pytest.mark.parametrize('failure', ['first-observation', 'second-observation', 'installation', 'expiry', 'changed-journal', 'audit-conflict'])
def test_failure_never_changes_original_database(world, monkeypatch, failure):
    w = world
    decision = w.port.prepare()['decision']
    envelope = approved(w, decision)
    before = containment.journal_bytes(w.directory)
    calls = []
    original_observe = w.port.observe_empty
    def observe(lease):
        calls.append(1)
        original_observe(lease)
        if failure in ('first-observation', 'second-observation') and len(calls) == (1 if failure == 'first-observation' else 2):
            raise DockerError('empty-host-unverified')
        if failure == 'expiry' and len(calls) == 2:
            w.port.clock = lambda: decision['expires'] + 1
        if failure == 'changed-journal' and len(calls) == 2:
            # Simulate detection of change, without corrupting the retained fixture.
            monkeypatch.setattr(containment, 'journal_bytes', lambda directory: before + b'changed')
    w.port.observe_empty = observe
    if failure == 'installation':
        def broken(): raise DockerError('installation-changed')
        w.port.installation.verify = broken
    if failure == 'audit-conflict':
        target = w.state / ('bootstrap-containment-' + decision['id'])
        target.mkdir(mode=0o700)
        (target / 'unexpected').write_text('preserve me')
    with pytest.raises((DockerError, JournalError)):
        w.port.execute(envelope)
    assert (w.directory / 'journal.sqlite3').read_bytes() == before


def test_atomic_failure_rolls_back_and_same_containment_can_finish(world, monkeypatch):
    w = world
    envelope = approved(w)
    before = containment.journal_bytes(w.directory)
    original = Journal.validate
    def fail_after_update(journal):
        original(journal)
        if journal.rows()[0]['run_status'] == 'contained':
            raise JournalError('injected-before-commit')
    monkeypatch.setattr(Journal, 'validate', fail_after_update)
    with pytest.raises(JournalError): w.port.execute(envelope)
    assert containment.journal_bytes(w.directory) == before
    monkeypatch.setattr(Journal, 'validate', original)
    assert w.port.execute(envelope)['status'] == 'contained'


def test_lost_reply_reports_commit_without_rewriting_or_reusing_nonce(world, monkeypatch):
    w = world
    envelope = approved(w)
    original = Journal.contain_empty_bootstrap
    def lost_reply(journal, *args):
        original(journal, *args)
        raise RuntimeError('reply lost after commit')
    monkeypatch.setattr(Journal, 'contain_empty_bootstrap', lost_reply)
    with pytest.raises(RuntimeError): w.port.execute(envelope)
    before = containment.journal_bytes(w.directory)
    assert w.port.execute(envelope)['status'] == 'contained'
    assert containment.journal_bytes(w.directory) == before
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        with pytest.raises(JournalError):
            journal.consume_authorization(ApprovalClaims(w.candidate.identity, 'operator', 1, 301, w.run), w.candidate)
        next_candidate = replace(w.candidate, manager_sha='f'*40)
        nonce = '3'*32
        journal.consume_authorization(ApprovalClaims(next_candidate.identity, 'operator', 1, 301, nonce), next_candidate)
        journal.prepare(nonce, 'manager-bootstrap', next_candidate.manager_sha,
                        (Intent('gateway', None, next_candidate.image, next_candidate.runtime_hash.removeprefix('sha256:')),))
        assert len(journal.rows()) == 2 and len(journal.incomplete()) == 1
        journal.validate()


@pytest.mark.parametrize('state', ['active', 'succeeded', 'other-application', 'other-target', 'previous-image', 'extra-event'])
def test_journal_eligibility_is_narrow(world, state):
    w = world
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        if state in ('active', 'succeeded'):
            journal.db.execute('update runs set status=?', (state,))
        elif state == 'other-application': journal.db.execute("update runs set application='project-router'")
        elif state == 'other-target': journal.db.execute("update attempts set target=?", (w.candidate.image.replace('c'*64, 'd'*64),))
        elif state == 'previous-image': journal.db.execute('update attempts set previous=target')
        else:
            journal.transition(w.run, 'gateway', 'verifying', 'mutation-completed')
        with pytest.raises(JournalError): journal.contain_empty_bootstrap(w.run, w.candidate.identity)


def test_normal_transition_and_missing_or_unsafe_journal_cannot_bypass(world):
    w = world
    with w.lock() as lease, Journal(w.directory, lease) as journal:
        with pytest.raises(JournalError):
            journal.transition(w.run, 'gateway', 'contained', 'empty-bootstrap-contained')
    db = w.directory / 'journal.sqlite3'
    original = db.read_bytes()
    db.chmod(0o644)
    with pytest.raises(DockerError): w.port.prepare()
    db.chmod(0o600)
    sidecar = Path(str(db) + '-wal')
    sidecar.write_bytes(b'preserve')
    with pytest.raises(DockerError): w.port.prepare()
    assert db.read_bytes() == original and sidecar.read_bytes() == b'preserve'


def test_missing_database_is_not_initialized(world):
    w = world
    empty = w.directory.parent / 'empty-journal'
    empty.mkdir(mode=0o700)
    w.port.directory = empty
    with pytest.raises(JournalError): w.port.prepare()
    assert list(empty.iterdir()) == [] and list(w.state.iterdir()) == []


def test_audit_loss_or_tampering_never_releases_another_operation(world):
    w = world
    decision = w.port.prepare()['decision']
    envelope = approved(w, decision)
    w.port.execute(envelope)
    before = containment.journal_bytes(w.directory)
    audit = w.state / ('bootstrap-containment-' + decision['id'])
    (audit / 'original.sqlite3').write_bytes(b'changed')
    with pytest.raises(DockerError): w.port.execute(envelope)
    assert containment.journal_bytes(w.directory) == before
