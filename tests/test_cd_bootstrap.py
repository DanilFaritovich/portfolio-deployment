"""Offline first-phase Actions/bridge contract and genuine operator signatures."""
import base64
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from installation import bootstrap_entry
from scripts.cd import bootstrap, bridge, bootstrap_runner
from scripts.cd.policy import pin
from scripts.docker_adapter import DockerError
from scripts.production_integration.operator_approval import authenticate_signed, canonical
from test_cd import identities, candidate, approval, REPO
from test_production_installation import package, SHA
from test_production_execution import setup as engine_setup, router, registration, approve
from scripts.deployment_journal import Journal


def test_standalone_entry_refuses_checkout_before_imports(monkeypatch):
    monkeypatch.setattr(bootstrap_entry.sys, 'argv', ['bootstrap_entry.py'])
    assert bootstrap_entry.main() == 2


@pytest.mark.parametrize('role', ['automation', 'observer'])
def test_bootstrap_never_delegates_operator_authority(identities, role):
    c = candidate('first-bootstrap')
    executor = SimpleNamespace(verifier=SimpleNamespace(authenticate=lambda raw:
        authenticate_signed(raw, {'operator': identities['operator'][1]})), clock=lambda:__import__('time').time(),
        run=lambda *args: pytest.fail('unapproved executor invocation'))
    installed = SimpleNamespace(manager_sha=c.manager_sha, verify=lambda:None)
    request = {'op':'bootstrap-execute', 'scope':c.scope, 'manager_sha':c.manager_sha,
               'candidate':asdict(c), 'envelope':base64.b64encode(approval(c, identities, role=role)).decode()}
    with pytest.raises(DockerError):
        bootstrap_entry.run(request, installed, executor)


@pytest.mark.parametrize('failure', [None, 'scope', 'manager', 'candidate', 'expired'])
def test_original_operator_envelope_reaches_unchanged_executor(identities, failure):
    c = candidate('first-bootstrap')
    envelope = approval(c, identities, role='operator', lifetime=-1 if failure == 'expired' else 295)
    calls = []
    executor = SimpleNamespace(verifier=SimpleNamespace(authenticate=lambda raw:
        authenticate_signed(raw, {'operator': identities['operator'][1]})), clock=lambda:__import__('time').time(),
        run=lambda *args:(calls.append(args) or SimpleNamespace(status='succeeded', candidate=c.identity, reasons=())))
    installed = SimpleNamespace(manager_sha=c.manager_sha, verify=lambda:None)
    request = {'op':'bootstrap-execute', 'scope':c.scope, 'manager_sha':c.manager_sha,
               'candidate':asdict(c), 'envelope':base64.b64encode(envelope).decode()}
    if failure == 'scope': request['scope'] = 'update-project-router'
    if failure == 'manager': request['manager_sha'] = 'c'*40
    if failure == 'candidate': request['candidate'] = asdict(replace(c, plan_hash='sha256:'+'e'*64))
    if failure:
        with pytest.raises(DockerError): bootstrap_entry.run(request, installed, executor)
        assert calls == []
    else:
        assert bootstrap_entry.run(request, installed, executor)['status'] == 'succeeded'
        assert calls == [(c, envelope)]


@pytest.mark.parametrize('scope', bootstrap.SCOPES)
def test_prepare_is_locked_and_never_calls_executor(monkeypatch, scope):
    from contextlib import contextmanager
    c = candidate(scope)
    lease = object()
    active = []
    @contextmanager
    def lock():
        active.append(True)
        try: yield lease
        finally: active.pop()
    monkeypatch.setattr('scripts.production_integration.locking.production_host_lock', lock)
    def prepare(scope, supplied):
        assert active and supplied is lease and scope == c.scope
        return SimpleNamespace(candidate=c)
    executor = SimpleNamespace(preflight=SimpleNamespace(prepare_locked=prepare),
                               run=lambda *args:pytest.fail('prepare attempted execution'))
    result = bootstrap_entry.run({'op':'bootstrap-prepare','scope':scope,'manager_sha':c.manager_sha},
        SimpleNamespace(manager_sha=c.manager_sha, verify=lambda:None), executor)
    assert result == {'status':'prepared','candidate':asdict(c)}


@pytest.fixture
def install_world(tmp_path, monkeypatch):
    raw = package()
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path/'manager')
    monkeypatch.setattr(bootstrap, 'BASE', tmp_path)
    monkeypatch.setattr(bootstrap, 'CAPSULE', tmp_path)
    monkeypatch.setattr(bootstrap, 'protected', lambda *a, **k:None)
    trust = tmp_path/'operator-keys.json'
    trust.write_bytes(b'independent pinned trust')
    monkeypatch.setattr('scripts.production_integration.operator_approval.TRUST', trust)
    monkeypatch.setattr('scripts.production_integration.operator_approval.trust_keys', lambda raw:{'operator':'trusted'})
    (tmp_path/'capsule.json').write_bytes(canonical({'files':{str(trust):pin(trust.read_bytes())}}))
    (tmp_path/'installation-decision.json').write_bytes(canonical({'manager_sha':SHA,'bundle_sha256':pin(raw)}))
    monkeypatch.setattr('scripts.cd.policy.read_policy', lambda keys:SimpleNamespace(document={'initial_manager_sha':SHA}))
    monkeypatch.setattr(bridge, 'manager_verifier', lambda policy:SimpleNamespace(verify=lambda data:
        SimpleNamespace(manager_sha=SHA,bundle_sha256=pin(data))))
    calls=[]
    monkeypatch.setattr(bootstrap, 'checked_process', lambda argv, **kwargs:
        (calls.append(argv) or {'status':'installed','manager_sha':SHA,'executorStarted':False}))
    return raw, calls, {'op':'bootstrap-install','length':len(raw),'manager_sha':SHA}


@pytest.mark.parametrize('failure', [None, 'existing', 'symlink', 'decision', 'conflict', 'framing', 'signature'])
def test_install_requires_independent_exact_review_and_never_updates(install_world, tmp_path, monkeypatch, failure):
    raw, calls, request = install_world
    if failure == 'existing': bootstrap.ROOT.mkdir()
    if failure == 'symlink': bootstrap.ROOT.symlink_to(tmp_path/'absent')
    if failure == 'decision': (tmp_path/'installation-decision.json').write_bytes(canonical({'manager_sha':SHA,'bundle_sha256':'a'*64}))
    if failure == 'conflict': (tmp_path/'bundle.zip').write_bytes(b'preserve foreign bytes')
    if failure == 'framing': raw += b'excess'
    if failure == 'signature':
        def reject(raw): raise DockerError('invalid release signature')
        monkeypatch.setattr(bridge, 'manager_verifier', lambda policy:SimpleNamespace(verify=reject))
    if failure:
        with pytest.raises(DockerError): bootstrap.install(request, io.BytesIO(raw))
        assert not calls
        if failure == 'conflict': assert (tmp_path/'bundle.zip').read_bytes() == b'preserve foreign bytes'
    else:
        assert bootstrap.install(request, io.BytesIO(raw))['status'] == 'installed'
        assert (tmp_path/'bundle.zip').read_bytes() == raw
        assert calls == [['/usr/bin/python3','-I','-S','-B',str(bootstrap.HELPER)]]


def test_helper_result_accepts_existing_json_output_and_fails_closed(monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda *a, **k:SimpleNamespace(
        returncode=0, stdout=b'{"status": "installed", "executorStarted": false}\n'))
    assert bootstrap.checked_process(['/fixed/helper'])['executorStarted'] is False
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda *a, **k:SimpleNamespace(
        returncode=2, stdout=b'{"status":"blocked"}'))
    with pytest.raises(DockerError): bootstrap.checked_process(['/fixed/helper'])


def test_observer_cannot_use_initial_install(monkeypatch):
    monkeypatch.setattr(bridge, 'installation', lambda:SimpleNamespace())
    monkeypatch.setattr(bridge, 'installed_policy', lambda installed:None)
    monkeypatch.setattr(bootstrap, 'install', lambda *a:pytest.fail('observer invoked installer'))
    with pytest.raises(DockerError): bridge.handle('observer', {'op':'bootstrap-install'}, io.BytesIO())


@pytest.mark.parametrize('failure', [None, 'blocked', 'recovery-required', 'rolled-back'])
def test_runner_passes_original_candidate_without_automation_signature(failure):
    c = asdict(candidate('first-bootstrap'))
    submitted=[]
    def call(request):
        submitted.append(request)
        return {'status':failure or 'succeeded'}
    client = SimpleNamespace(call=call, approval=lambda *a:pytest.fail('must never delegate first-phase authority'))
    document = {'candidate':c,'envelope':'operator signed envelope'}
    encoded = base64.b64encode(canonical(document)).decode()
    if failure:
        with pytest.raises(DockerError): bootstrap_runner.dispatch(client,'execute','first-bootstrap',c['manager_sha'],'.',encoded)
    else:
        assert bootstrap_runner.dispatch(client,'execute','first-bootstrap',c['manager_sha'],'.',encoded)['status']=='succeeded'
    assert submitted == [{'op':'bootstrap-execute','scope':'first-bootstrap','manager_sha':c['manager_sha'],**document}]


def test_workflow_gates_and_shared_serialization():
    # BaseLoader deliberately keeps GitHub's `on` key a string (YAML 1.2).
    workflow=yaml.load((REPO/'.github/workflows/production-bootstrap.yml').read_text(), Loader=yaml.BaseLoader)
    cd=yaml.load((REPO/'.github/workflows/cd-deploy.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['concurrency'] == cd['concurrency']
    job=workflow['jobs']['bootstrap']
    assert job['environment']=='production' and "refs/heads/main" in job['if'] and 'github.ref_protected' in job['if']
    assert workflow['permissions']=={'contents':'read','actions':'read'}
    checkout=next(s for s in job['steps'] if s.get('uses','').startswith('actions/checkout@'))
    assert checkout['with']['ref']=='${{ vars.CD_MANAGER_CLIENT_SHA }}'
    assert checkout['with']['persist-credentials']=='false'
    assert all('OPERATOR_KEY' not in str(s) for s in job['steps'])
    from scripts.production_integration.install_bundle import source_target
    assert source_target('scripts/cd/bootstrap.py') == 'scripts/cd/bootstrap.py'
    # Pre-bootstrap capsules select every newly introduced CD module; changing
    # that closure would make their independent Manager bundle review reject it.
    assert source_target('scripts/cd/bootstrap_runner.py') == 'scripts/cd/bootstrap_runner.py'


def test_capsule_entry_uses_real_journal_and_rejects_replay(engine_setup):
    s = engine_setup
    c, envelope = approve(s)
    request = {'op':'bootstrap-execute','scope':c.scope,'manager_sha':c.manager_sha,
               'candidate':asdict(c),'envelope':base64.b64encode(envelope).decode()}
    result = bootstrap_entry.run(request, s.executor.installation, s.executor)
    assert result['status'] == 'succeeded'
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web')['run'] == '1'*32
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?", ('1'*32,)).fetchone()[0] == 'succeeded'
    before = list(s.world.calls)
    s.world.present, s.world.route = False, False
    assert bootstrap_entry.run(request, s.executor.installation, s.executor)['status'] == 'blocked'
    assert s.world.calls == before
