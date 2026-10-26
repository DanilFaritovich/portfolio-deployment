"""Exact root authorization and real signature/filesystem upgrade, offline only."""
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from scripts.cd import administrative as admin
from scripts.cd.policy import pin
from scripts.docker_adapter import DockerError
from scripts.production_integration.operator_approval import canonical
from test_cd import identities, disposable_directory_policy, upgrade_world, approval, change_revision, BASE, NEXT


@pytest.fixture
def world(tmp_path, monkeypatch, identities):
    manager, raw = upgrade_world(tmp_path, identities)
    capsule = tmp_path/'capsule'
    capsule.mkdir(mode=0o700)
    (capsule/'capsule.json').write_bytes(b'{}')
    path = tmp_path/'decision.json'
    state = {'database': 'absent', 'recovery': 'unestablished'}
    def private(p, limit):
        assert not p.is_symlink() and p.is_file() and p.stat().st_nlink == 1
        value = p.read_bytes()
        assert len(value) <= limit
        return value
    monkeypatch.setattr(admin, 'private', private)
    monkeypatch.setattr('scripts.cd.upgrade.protected', lambda p: private(p, 131072))
    monkeypatch.setattr(admin, 'require_production_lease', lambda lease: None)
    monkeypatch.setattr(admin, 'ReadOnlyJournalSource', lambda: SimpleNamespace(read=lambda lease: state.copy()))
    from scripts.cd import quiescence
    reviews = []
    monkeypatch.setattr(quiescence, 'initial_review', lambda *args: reviews.append(args))
    decision = {'version': 1, 'id': '7'*32, 'machine_id': manager.policy.document['host'],
        'daemon': manager.policy.document['daemon'], 'from_sha': BASE, 'to_sha': NEXT,
        'inventory_sha256': pin((manager.root/'installation.json').read_bytes()),
        'capsule_sha256': pin(b'{}'), 'policy_id': manager.policy.identity, 'bundle_sha256': pin(raw),
        'not_before': int(time.time())-1, 'expires': int(time.time())+300}
    def load():
        path.write_bytes(canonical(decision))
        port = admin.AdministrativeUpgrade(manager, decision=path, capsule=capsule)
        manager.quiescent = port.check
        return port
    return SimpleNamespace(manager=manager, raw=raw, decision=decision, load=load,
                           state=state, identities=identities, reviews=reviews)


def test_preparation_is_read_only_and_operator_upgrade_retains_old_tree(world):
    w = world
    port = w.load()
    candidate = port.prepare(w.raw)
    assert not list(w.manager.state.iterdir())
    result = port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator'))
    assert result['status'] == 'succeeded'
    assert Path(result['retained']).is_dir()
    assert w.state == {'database': 'absent', 'recovery': 'unestablished'}
    record = json.loads(next(w.manager.state.glob('admin-upgrade-*.json')).read_bytes())
    assert record['status'] == 'succeeded' and record['candidate'] == asdict(candidate)


def test_admin_upgrade_after_successful_initial_manager_update_without_journal(world):
    w = world
    first = w.manager.prepare(w.raw)
    assert w.manager.run(w.raw, first, approval(first, w.identities))['status'] == 'succeeded'
    w.manager.installation = SimpleNamespace(manager_sha=NEXT, verify=lambda: None)
    w.raw = change_revision(w.raw, 'c'*40)
    w.decision.update(from_sha=NEXT, to_sha='c'*40, bundle_sha256=pin(w.raw),
                      inventory_sha256=pin((w.manager.root/'installation.json').read_bytes()))
    port = w.load()
    candidate = port.prepare(w.raw)
    result = port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator', nonce='6'*32))
    assert result['status'] == 'succeeded' and result['manager_sha'] == 'c'*40
    assert w.state == {'database': 'absent', 'recovery': 'unestablished'}


@pytest.mark.parametrize('field', ['expired', 'from_sha', 'to_sha', 'machine_id', 'daemon',
                                  'inventory_sha256', 'capsule_sha256', 'policy_id', 'bundle_sha256'])
def test_wrong_or_expired_exact_decision_blocks_before_intent(world, field):
    w = world
    if field == 'expired':
        w.decision['expires'] = 1
    else:
        w.decision[field] = '0'*len(w.decision[field])
    with pytest.raises(DockerError):
        w.load().prepare(w.raw)
    assert not list(w.manager.state.iterdir())


@pytest.mark.parametrize('boundary', ['journal-present', 'journal-lost', 'review-revoked', 'decision-replaced'])
def test_persistent_history_and_fresh_authority_are_not_bypassed(world, monkeypatch, boundary):
    w = world
    port = w.load()
    if boundary == 'journal-present':
        w.state['database'] = 'present'
    elif boundary == 'journal-lost':
        (w.manager.state/'journal-established.json').write_bytes(b'preserve')
    elif boundary == 'review-revoked':
        from scripts.cd import quiescence
        def reject(*args): raise DockerError('review-revoked')
        monkeypatch.setattr(quiescence, 'initial_review', reject)
    else:
        port.path.write_bytes(b'{}')
    with pytest.raises(DockerError): port.prepare(w.raw)
    assert not list(w.manager.state.glob('upgrade-*.json'))


def test_delegated_automation_cannot_sign_administrative_upgrade(world):
    w = world
    port = w.load()
    candidate = port.prepare(w.raw)
    with pytest.raises(DockerError):
        port.execute(w.raw, candidate, approval(candidate, w.identities))
    assert not list(w.manager.state.iterdir())


def test_defined_health_failure_rolls_back_and_consumes_admin_permission(world):
    w = world
    w.manager.health = lambda root, sha: sha == BASE
    port = w.load()
    candidate = port.prepare(w.raw)
    assert port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator'))['status'] == 'rolled-back'
    with pytest.raises(DockerError, match='consumed'):
        w.load().prepare(w.raw)


def test_interrupted_update_is_fenced_and_fresh_operator_recovery_never_replays(world):
    w = world
    def interrupt(*args): raise RuntimeError('interrupted before exchange')
    w.manager.swap = interrupt
    port = w.load()
    candidate = port.prepare(w.raw)
    with pytest.raises(RuntimeError):
        port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator'))
    with pytest.raises(DockerError): w.load().prepare(w.raw)
    w.decision.update(id='8'*32, to_sha=BASE)
    recovered = w.load().recover('7'*32, approval(candidate, w.identities, role='operator', nonce='6'*32))
    assert recovered == {'status': 'rolled-back', 'manager_sha': BASE}
    records = [json.loads(p.read_bytes()) for p in w.manager.state.glob('admin-upgrade-*.json')]
    assert {r['status'] for r in records} == {'recovered', 'succeeded'}
    assert any('original' in r and r['original']['status'] == 'recovery-required' for r in records)


@pytest.mark.parametrize('accepted', [False, True])
def test_recovery_handles_exchange_and_lost_final_administrative_receipt(world, accepted):
    w = world
    port = w.load()
    candidate = port.prepare(w.raw)
    if accepted:
        original = port.finish
        def interrupt(status):
            if status == 'succeeded': raise RuntimeError('lost terminal after verified upgrade')
            original(status)
        port.finish = interrupt
    else:
        w.manager.health = lambda root, sha: True if sha == BASE else None
    with pytest.raises(RuntimeError):
        port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator'))
    w.manager.installation = SimpleNamespace(manager_sha=NEXT, verify=lambda: None)
    w.decision.update(id='8'*32, from_sha=NEXT, to_sha=NEXT if accepted else BASE,
        inventory_sha256=pin((w.manager.root/'installation.json').read_bytes()))
    result = w.load().recover('7'*32, approval(candidate, w.identities, role='operator', nonce='6'*32))
    assert result['manager_sha'] == (NEXT if accepted else BASE)
    assert result['status'] == ('succeeded' if accepted else 'rolled-back')


def test_interrupted_recovery_can_be_resolved_without_replaying_upgrade(world):
    w = world
    w.manager.health = lambda root, sha: True if sha == BASE else None
    port = w.load()
    candidate = port.prepare(w.raw)
    with pytest.raises(RuntimeError):
        port.execute(w.raw, candidate, approval(candidate, w.identities, role='operator'))
    w.manager.installation = SimpleNamespace(manager_sha=NEXT, verify=lambda: None)
    w.decision.update(id='8'*32, from_sha=NEXT, to_sha=BASE,
        inventory_sha256=pin((w.manager.root/'installation.json').read_bytes()))
    original = w.manager.recover
    def interrupt(*args):
        original(*args)
        raise RuntimeError('lost recovery terminal')
    w.manager.recover = interrupt
    with pytest.raises(RuntimeError):
        w.load().recover('7'*32, approval(candidate, w.identities, role='operator', nonce='6'*32))
    w.manager.installation = SimpleNamespace(manager_sha=BASE, verify=lambda: None)
    w.decision.update(id='9'*32, from_sha=BASE,
        inventory_sha256=pin((w.manager.root/'installation.json').read_bytes()))
    w.manager.recover = lambda *args: pytest.fail('recovery replayed a completed restoration')
    result = w.load().recover('8'*32, approval(candidate, w.identities, role='operator', nonce='4'*32))
    assert result == {'status': 'rolled-back', 'manager_sha': BASE}
    records = [json.loads(p.read_bytes()) for p in w.manager.state.glob('admin-upgrade-*.json')]
    assert sorted(r['status'] for r in records) == ['recovered', 'recovered', 'succeeded']
