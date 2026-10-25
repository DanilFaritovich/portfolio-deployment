"""Offline first-migration gates; real source/lock integration in local-source tests."""
from types import SimpleNamespace

import pytest

from scripts.cd import quiescence, bridge
from scripts.docker_adapter import DockerError
from test_cd import (identities, disposable_directory_policy, policy_document, signed_policy, upgrade_world,
                     approval, verify_tree, BASE, NEXT)


@pytest.fixture
def world(tmp_path, monkeypatch):
    state = {'database': 'absent', 'recovery': 'unestablished'}
    records, reviews = [], []
    manager = SimpleNamespace(state=tmp_path, root=tmp_path / 'manager',
        policy=SimpleNamespace(document={'initial_manager_sha': BASE, 'host': '2'*32,
            'daemon': 'daemon', 'initial_journal': 'reviewed-empty-host'}, current=lambda: None),
        installation=SimpleNamespace(manager_sha=BASE, verify=lambda: None),
        recovery_report=lambda: records)
    lease = object()
    def require_lease(value):
        if value is not lease: raise DockerError('invalid lease')
    monkeypatch.setattr(quiescence, 'require_production_lease', require_lease)
    monkeypatch.setattr(quiescence, 'ReadOnlyJournalSource', lambda: SimpleNamespace(read=lambda value: dict(state)))
    monkeypatch.setattr(quiescence, 'protected', lambda path: None)
    monkeypatch.setattr(quiescence, 'initial_review', lambda *args: reviews.append(args))
    return manager, lease, state, records, reviews


def test_initial_absence_is_explicit_and_never_initializes_journal(world):
    manager, lease, state, records, reviews = world
    quiescence.check(manager, lease)
    assert len(reviews) == 1 and not list(manager.state.iterdir())
    manager.policy.document.pop('initial_journal')
    with pytest.raises(DockerError, match='not-authorized'): quiescence.check(manager, lease)
    assert not list(manager.state.iterdir())


@pytest.mark.parametrize('state', [{}, {'database': 'unknown'}, {'database': 'absent'},
    {'database': 'absent', 'recovery': 'unestablished', 'incomplete': ['lost']},
    {'database': 'present'}, {'database': 'present', 'incomplete': []},
    {'database': 'present', 'incomplete': ['run'], 'authorizations': []},
    {'database': 'present', 'incomplete': [], 'authorizations': ['nonce']}])
def test_ambiguous_and_incomplete_states_fail_closed(world, state):
    manager, lease, actual, records, reviews = world
    actual.clear()
    actual.update(state)
    with pytest.raises(DockerError, match='recovery-required'): quiescence.check(manager, lease)
    assert not reviews


def test_present_journal_latches_history_even_on_recovery_failure(world):
    manager, lease, state, records, reviews = world
    state.update(database='present', incomplete=['run'], authorizations=[])
    with pytest.raises(DockerError): quiescence.check(manager, lease)
    marker = manager.state / 'journal-established.json'
    before, inode = marker.read_bytes(), marker.stat().st_ino
    state['incomplete'] = []
    quiescence.check(manager, lease)
    assert (marker.read_bytes(), marker.stat().st_ino) == (before, inode)
    state.clear()
    state.update(database='absent', recovery='unestablished')
    with pytest.raises(DockerError, match='history-lost'): quiescence.check(manager, lease)
    assert not reviews


@pytest.mark.parametrize('status', ['succeeded', 'no-op'])
def test_completed_manager_history_forbids_missing_database(world, status):
    manager, lease, state, records, reviews = world
    records.append({'from': BASE, 'status': status})
    with pytest.raises(DockerError, match='history-lost'): quiescence.check(manager, lease)


def test_pending_initial_recovery_requires_exact_tree_and_fresh_review(world, monkeypatch):
    manager, lease, state, records, reviews = world
    manager.installation.manager_sha = NEXT
    active = {'manager_sha': NEXT, 'files': []}
    monkeypatch.setattr(quiescence, 'verify_tree', lambda path: active)
    with pytest.raises(DockerError, match='initial-manager-required'): quiescence.check(manager, lease)
    records.append({'from': BASE, 'status': 'switch-intent', 'new_inventory': active.copy()})
    quiescence.check(manager, lease)
    assert reviews
    records[0]['new_inventory'] = {}
    with pytest.raises(DockerError, match='initial-manager-required'): quiescence.check(manager, lease)


def test_expired_review_source_failure_and_lease_are_not_bypassed(world, monkeypatch):
    manager, lease, state, records, reviews = world
    with pytest.raises(DockerError, match='invalid lease'): quiescence.check(manager, object())
    def fail(*args): raise DockerError('production-review-expired')
    monkeypatch.setattr(quiescence, 'initial_review', fail)
    with pytest.raises(DockerError, match='review-expired'): quiescence.check(manager, lease)
    monkeypatch.setattr(quiescence, 'ReadOnlyJournalSource', lambda: SimpleNamespace(read=fail))
    with pytest.raises(DockerError, match='review-expired'): quiescence.check(manager, lease)


def test_policy_permission_requires_real_operator_signature(identities):
    doc = policy_document(identities)
    doc['initial_journal'] = 'reviewed-empty-host'
    assert signed_policy(doc, identities).document['initial_journal'] == 'reviewed-empty-host'
    doc['initial_journal'] = True
    with pytest.raises(DockerError, match='initial-journal-policy-invalid'): signed_policy(doc, identities)


def test_bridge_uses_gate_instead_of_indexing_absent_fields(world, monkeypatch):
    manager, lease, state, records, reviews = world
    monkeypatch.setattr(bridge, 'manager_verifier', lambda policy: None)
    actual = bridge.updater(manager.installation, manager.policy)
    actual.state = manager.state
    actual.quiescent(lease)
    assert reviews


def test_preexchange_quiescence_failure_preserves_sealed_tree(tmp_path, identities):
    import os
    updater, raw = upgrade_world(tmp_path, identities)
    before = verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())
    calls = []
    def check(lease):
        calls.append(lease)
        if len(calls) == 2: raise DockerError('fresh-review-expired')
    updater.quiescent = check
    candidate = updater.prepare(raw)
    with pytest.raises(DockerError, match='fresh-review-expired'):
        updater.run(raw, candidate, approval(candidate, identities))
    assert len(calls) == 2
    assert verify_tree(updater.root, active_root=updater.root, uid=os.geteuid()) == before
    with pytest.raises(DockerError, match='recovery-required'): updater.fenced()


@pytest.mark.parametrize('phase,host,collect_error', [
    ('empty-host', '2'*32, None), ('rollout', '2'*32, None),
    ('empty-host', '3'*32, None), ('empty-host', '2'*32, 'nonempty host')])
def test_initial_review_uses_fresh_complete_empty_host_observation(monkeypatch, phase, host, collect_error):
    policy = SimpleNamespace(document={'host': '2'*32, 'daemon': 'daemon'})
    installed = object()
    calls = []
    review = SimpleNamespace(document={'phase': phase}, policy=SimpleNamespace(host=host, daemon='daemon'),
                             current=lambda: calls.append('current'))
    monkeypatch.setattr(quiescence, 'AdministrativePhaseReview', lambda value: review)
    monkeypatch.setattr(quiescence, 'installed_engine', lambda value, contract: calls.append(contract) or 'engine')
    monkeypatch.setattr(quiescence, 'LocalHostSources', lambda *args: 'local')
    def reader(*args):
        assert args == (installed, 'engine', 'local', review.policy, review)
        def collect():
            calls.append('collect')
            if collect_error: raise DockerError(collect_error)
        return SimpleNamespace(collect=collect)
    monkeypatch.setattr(quiescence, 'EmptyHostReader', reader)
    if phase != 'empty-host' or host != '2'*32 or collect_error:
        with pytest.raises(DockerError): quiescence.initial_review(installed, policy)
        assert 'current' not in calls
    else:
        quiescence.initial_review(installed, policy)
        assert calls[-2:] == ['collect', 'current']


def test_changed_journal_during_initial_review_blocks(world, monkeypatch):
    manager, lease, state, records, reviews = world
    monkeypatch.setattr(quiescence, 'initial_review', lambda *args: state.update(database='present'))
    with pytest.raises(DockerError, match='journal-changed'): quiescence.check(manager, lease)


def test_absent_journal_first_exchange_crash_retains_signed_recovery(tmp_path, identities, monkeypatch):
    import os
    def crash(*args): raise TimeoutError('unknown health')
    updater, raw = upgrade_world(tmp_path, identities, health=crash)
    doc = dict(updater.policy.document, initial_journal='reviewed-empty-host')
    updater.policy = signed_policy(doc, identities)
    monkeypatch.setattr(quiescence, 'require_production_lease', lambda lease: None)
    monkeypatch.setattr(quiescence, 'ReadOnlyJournalSource', lambda: SimpleNamespace(
        read=lambda lease: {'database': 'absent', 'recovery': 'unestablished'}))
    monkeypatch.setattr(quiescence, 'verify_tree', lambda root: verify_tree(
        root, active_root=updater.root, uid=os.geteuid()))
    reviews = []
    monkeypatch.setattr(quiescence, 'initial_review', lambda installed, policy: reviews.append(installed.manager_sha))
    updater.quiescent = lambda lease: quiescence.check(updater, lease)
    before = verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())
    candidate = updater.prepare(raw)
    with pytest.raises(TimeoutError): updater.run(raw, candidate, approval(candidate, identities))
    with pytest.raises(DockerError, match='recovery-required'): updater.fenced()
    updater.installation.manager_sha = NEXT
    updater.health = lambda *args: True
    with pytest.raises(DockerError):
        updater.recover('5'*32, approval(candidate, identities, nonce='6'*32))
    assert updater.recover('5'*32, approval(candidate, identities, role='operator', nonce='7'*32))['status'] == 'rolled-back'
    assert reviews[-1] == NEXT
    assert verify_tree(updater.root, active_root=updater.root, uid=os.geteuid()) == before
    assert (updater.state / ('upgrade-'+'5'*32+'.json')).exists()
    assert (updater.state / ('recovery-'+'7'*32+'.json')).exists()
    assert not (updater.state / 'journal-established.json').exists()
