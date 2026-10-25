"""CD public acceptance exercises the SAME engine, journal and safe rollback."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import uuid

import pytest

from scripts.cd.release import ProjectedContent, ImageRelease
from scripts.deployment import execute
from scripts.deployment_journal import locked_journal
from scripts.deployment_lifecycle import DeploymentLifecycle
from scripts.docker_adapter import DockerError
from scripts.production_integration.execution import ProductionLifecycle
from scripts.readiness import ProbeResult
from scripts.reconciliation import DesiredState, build_reconciliation_plan
from scripts.reconciliation import encode
from test_cd import identities, signed_policy, policy_document, REPO, BASE, IMAGE
from test_readiness_recovery import World, Probes, Clock, POLICY, run, upgraded
from test_deployment import image


@pytest.mark.parametrize('failure', [None, 'unhealthy', 'external', 'rollback-external'])
def test_public_gate_rollback_lkg_and_persistent_state(tmp_path, identities, failure):
    standing = signed_policy(policy_document(identities), identities)
    installation = SimpleNamespace(manager_sha=BASE, verify=lambda: None)
    release = ImageRelease(BASE, IMAGE, 'c'*64, '1', '1')
    original = ProjectedContent(installation, standing, release, root=REPO)()[0].applications[0]
    # The existing World fixture models plain HTTP image defaults. Runtime
    # security/admission and real image configs have their own independent tests.
    spec = original.spec
    spec['components'][0].pop('runtimeSecurity')
    original = replace(original, spec_json=encode(spec))
    world = World(original)
    run(world, tmp_path)
    before_lock = (tmp_path/'l/host.lock').stat().st_ino
    marker = tmp_path/'production-like-state'
    marker.write_bytes(b'certificate-and-persistent-state-marker')
    world.app = upgraded(original)
    world.desired = DesiredState('b'*40, (world.app,))
    world.extra_images = (image(IMAGE),)
    new_image = world.app.spec['components'][0]['image']
    class Public:
        calls = []
        def verify(self, app):
            current = app.spec['components'][0]['image']
            self.calls.append(current)
            if failure in ('external','rollback-external') and current == new_image or failure == 'rollback-external' and current == IMAGE:
                raise DockerError('public-independent-external-proof-invalid')
            return True
    public = Public()
    class Probe(Probes):
        def probe(self, component, *args, **kwargs):
            return ProbeResult(True, 503 if failure == 'unhealthy' and component['image'] == new_image else 200)
    class Gateway:
        def activate(self, *args, **kwargs):
            return False
    class ControlledLifecycle(ProductionLifecycle):
        @contextmanager
        def open(self, lease, adapter, provider):
            with DeploymentLifecycle.open(self, lease, adapter, provider):
                yield self
    clock = Clock()
    lifecycle = ControlledLifecycle(SimpleNamespace(directory=tmp_path/'j'),
        SimpleNamespace(claims=SimpleNamespace(nonce=uuid.uuid4().hex)), Probe(), gateway=Gateway(),
        public_acceptance=public, side_effects_excluded=True, policy=POLICY, clock=clock.now, sleep=clock.sleep)
    adapter = world.adapter()
    observed = world.provider.refresh()[1]
    results = execute(world.desired, observed, build_reconciliation_plan(world.desired, observed), original.id,
                      adapter, world.provider, lock_directory=tmp_path/'l', lifecycle=lifecycle)
    assert marker.read_bytes() == b'certificate-and-persistent-state-marker'
    assert (tmp_path/'l/host.lock').stat().st_ino == before_lock
    with locked_journal(tmp_path/'j', lock_directory=tmp_path/'l') as journal:
        good = journal.last_good(original.id, 'web')
        if failure is None:
            assert results[0].release_success and good['target'] == new_image
            assert public.calls == [new_image]
        else:
            assert not results[0].release_success and good['target'] == IMAGE
            assert len(adapter.calls) == 2
            assert world.actual.applications[0].spec['components'][0]['image'] == IMAGE
            assert bool(journal.incomplete()) == (failure == 'rollback-external')


def test_update_preflight_requires_image_only_and_existing_baseline(tmp_path, identities, monkeypatch):
    from scripts.production_integration.candidate import CandidatePreflight, HostContracts
    from scripts.reconciliation import encode
    standing = signed_policy(policy_document(identities), identities)
    installation = SimpleNamespace(manager_sha=BASE, verify=lambda: None)
    app = ProjectedContent(installation, standing, ImageRelease(BASE,IMAGE,'c'*64,'1','1'),root=REPO)()[0].applications[0]
    spec = app.spec
    spec['components'][0].pop('runtimeSecurity')
    app = replace(app, spec_json=encode(spec))
    # The synthetic World uses test-session project names. Production resource
    # admission is independently covered by test_production_ports/execution.
    monkeypatch.setattr('scripts.production_integration.candidate.preflight_resources', lambda *args: ())
    world = World(app, present=True)
    contracts = HostContracts('a'*32,'daemon',0,encode({'network':[]}),encode({'storage':[]}),encode({'recovery':[]}),True)
    preflight = CandidatePreflight(manager_sha=world.desired.git_revision, producer_sha=BASE,image=IMAGE,
        content_reader=lambda:(world.desired,b'manifest',b'policy'), engine=SimpleNamespace(identity=lambda:('a'*32,'daemon')),
        contracts=SimpleNamespace(collect=lambda:contracts),attestor=SimpleNamespace(verify=lambda c,i:c),
        provider=world.provider,clock=lambda:0)
    assert preflight.prepare('update-project-router').candidate.scope == 'update-project-router'
    spec = app.spec
    spec['components'][0]['port'] += 1
    world.desired = DesiredState(world.desired.git_revision,(replace(app,spec_json=encode(spec)),))
    with pytest.raises(DockerError):
        preflight.prepare('update-project-router')


def test_installed_digest_noop_preserves_lkg_journal_and_lock_inode(tmp_path,identities,monkeypatch):
    import sys
    import hashlib
    from scripts.cd import application
    from scripts.deployment import host_lock
    standing=signed_policy(policy_document(identities),identities)
    installation=SimpleNamespace(manager_sha=BASE,verify=lambda:None)
    app=ProjectedContent(installation,standing,ImageRelease(BASE,IMAGE,'c'*64,'1','1'),root=REPO)()[0].applications[0]
    spec=app.spec
    spec['components'][0].pop('runtimeSecurity')
    app=replace(app,spec_json=encode(spec))
    world=World(app)
    run(world,tmp_path)
    journal=tmp_path/'j/journal.sqlite3'
    before=hashlib.sha256(journal.read_bytes()).hexdigest()
    inode=(tmp_path/'l/host.lock').stat().st_ino
    desired,observed,_=world.provider.refresh()
    prepared=SimpleNamespace(desired=desired,observed=observed,plan=build_reconciliation_plan(desired,observed))
    called=[]
    executor=SimpleNamespace(preflight=SimpleNamespace(prepare_locked=lambda *args:prepared))
    monkeypatch.setitem(sys.modules,'bindings',SimpleNamespace(POLICY=None))
    monkeypatch.setattr(application,'installed_policy',lambda installed:standing)
    monkeypatch.setattr(application,'compose',lambda *args:(executor,SimpleNamespace(verify=lambda app:called.append(app))))
    monkeypatch.setattr(application,'production_host_lock',lambda:host_lock(tmp_path/'l'))
    result=application.handle(installation,{'op':'prepare-update','release':
        {'source_sha':BASE,'image':IMAGE,'html_sha256':'c'*64,'run_id':'1','run_attempt':'1'}})
    assert result['status']=='no-op' and len(called)==1
    assert hashlib.sha256(journal.read_bytes()).hexdigest()==before
    assert (tmp_path/'l/host.lock').stat().st_ino==inode
