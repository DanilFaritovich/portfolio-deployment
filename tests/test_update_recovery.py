"""Signed update acceptance recovery: real SQLite, crypto, no production I/O."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import time

import pytest

from scripts.cd import update_recovery as recovery
from scripts.cd.release import ImageRelease
from scripts.cd.signatures import sign
from scripts.deployment import host_lock
from scripts.deployment_journal import Intent, Journal, JournalError
from scripts.deployment_lifecycle import runtime_identity
from scripts.docker_adapter import DockerError
from scripts.production_integration.authorization import ApprovalClaims
from scripts.production_integration.operator_approval import canonical
from scripts.readiness import ReadinessEvidence
from scripts.reconciliation import encode
from test_cd import identities, policy_document, signed_policy, candidate
from test_production_contracts import registration
from test_production_integration import router


@pytest.fixture
def operation(tmp_path, identities, router):
    old = router
    spec = old.spec
    spec['components'][0]['image'] = 'ghcr.io/danilfaritovich/project-router@sha256:'+'f'*64
    app = replace(old, spec_json=encode(spec))
    c = replace(candidate(), image=spec['components'][0]['image'])
    release = ImageRelease(c.producer_sha, c.image, 'e'*64, '123', '1')
    document = policy_document(identities)
    document['application']['initial_release']['image'] = old.spec['components'][0]['image']
    policy = signed_policy(document, identities)
    lock = lambda: host_lock(tmp_path/'locks')
    directory = tmp_path/'journal'
    verified = ReadinessEvidence('verified', 'readiness-verified', 2, 2)
    with lock() as lease, Journal(directory, lease) as journal:
        journal.prepare('1'*32, 'project-router', c.manager_sha,
                        (Intent('web', None, old.spec['components'][0]['image'], runtime_identity(old)),))
        journal.transition('1'*32, 'web', 'applying', 'mutation-started')
        journal.transition('1'*32, 'web', 'verifying', 'mutation-completed')
        journal.transition('1'*32, 'web', 'succeeded', 'readiness-verified', verified)
        journal.promote('1'*32)
        claims = ApprovalClaims(c.identity, 'automation', int(time.time())-1, int(time.time())+295, '2'*32)
        journal.consume_authorization(claims, c)
        journal.prepare('2'*32, 'project-router', c.manager_sha,
                        (Intent('web', old.spec['components'][0]['image'], c.image, runtime_identity(app)),))
        journal.transition('2'*32, 'web', 'applying', 'mutation-started')
        journal.transition('2'*32, 'web', 'verifying', 'mutation-completed')
        journal.transition('2'*32, 'web', 'recovery-required', 'fresh-state-unavailable')
        journal.db.execute("UPDATE runs SET reason=?,status='recovery-required' WHERE id=?", ('recovery-analysis-required','2'*32))
        journal.finish_authorization('2'*32, 'recovery-required')
        journal.validate()
    observation = {'application': 'project-router', 'component':'web', 'revision':c.manager_sha,
        'image':c.image, 'runtime':runtime_identity(app), 'route':app.spec['ingress'],
        'readiness':'verified', 'container_id':'c'*64, 'probes':2, 'consecutive':2}
    current = {'raw':None}
    (tmp_path/'audit').mkdir(mode=0o700)
    op = recovery.UpdateRecovery(installation=SimpleNamespace(manager_sha=c.manager_sha,verify=lambda:None),
        policy=policy, lock=lock, release_reader=lambda journal,run:(release,'d'*64),
        application_reader=lambda r:app, observe=lambda *a:dict(observation),
        inventories=lambda:{'inventory_sha256':'a'*64,'capsule_sha256':'b'*64},
        current_reader=lambda:current['raw'], current_writer=lambda raw:current.update(raw=raw),
        keys={'operator':identities['operator'][1]}, directory=directory,state=tmp_path/'audit')
    return SimpleNamespace(op=op,lock=lock,directory=directory,current=current,identities=identities,
        observation=observation,release=release,app=app,candidate=c)


def approval(s, decision=None, role='operator'):
    return sign(decision or s.op.prepare()['decision'],principal=role,
                key=s.identities[role][0],namespace=recovery.NAMESPACE)


def test_prepare_is_read_only(operation):
    s=operation
    before=(s.directory/'journal.sqlite3').read_bytes()
    result=s.op.prepare()
    assert result['status']=='prepared' and result['decision']['previous_run']=='1'*32
    assert (s.directory/'journal.sqlite3').read_bytes()==before
    assert not list(s.op.state.iterdir()) and s.current['raw'] is None


def test_verified_recovery_retains_exact_original_and_publishes_release(operation):
    s=operation
    before=(s.directory/'journal.sqlite3').read_bytes()
    with s.lock() as lease, Journal(s.directory,lease) as j:
        events=[tuple(r) for r in j.db.execute('SELECT * FROM events ORDER BY sequence')]
    envelope=approval(s)
    result=s.op.execute(envelope)
    assert result['status']=='succeeded' and not result['mutationReplayed']
    assert json.loads(s.current['raw'])==asdict(s.release)
    archive=next(s.op.state.glob('application-update-recovery-*'))
    assert (archive/'original.sqlite3').read_bytes()==before
    assert (archive/'approval.json').read_bytes()==envelope
    with s.lock() as lease, Journal(s.directory,lease) as j:
        j.validate()
        assert not j.incomplete() and j.last_good('project-router','web')['run']=='2'*32
        assert [tuple(r) for r in j.db.execute('SELECT * FROM events ORDER BY sequence')][:-1]==events[:-1]
        assert j.db.execute('SELECT state FROM authorizations WHERE nonce=?',('2'*32,)).fetchone()[0]=='succeeded'
    with pytest.raises(DockerError,match='single-pending-run'):
        s.op.execute(envelope)


@pytest.mark.parametrize('fault',['observer-key','expired','observation','journal','current'])
def test_changed_or_unapproved_facts_never_promote(operation,fault):
    s=operation
    decision=s.op.prepare()['decision']
    envelope=approval(s,decision,role='observer' if fault=='observer-key' else 'operator')
    if fault=='expired':s.op.clock=lambda:decision['expires']
    if fault=='observation':s.observation['container_id']='d'*64
    if fault=='journal':
        with s.lock() as lease, Journal(s.directory,lease) as j:
            with j.transaction():j.db.execute('UPDATE runs SET updated=updated+1 WHERE id=?',('2'*32,))
    if fault=='current':s.current['raw']=canonical(asdict(s.release))
    before=(s.directory/'journal.sqlite3').read_bytes()
    with pytest.raises(DockerError):s.op.execute(envelope)
    assert (s.directory/'journal.sqlite3').read_bytes()==before
    assert not list(s.op.state.iterdir())


def test_crash_after_release_file_write_keeps_journal_pending(operation):
    s=operation
    def interrupted(raw):
        s.current['raw']=raw
        raise OSError('controlled interruption')
    s.op.current_writer=interrupted
    before=(s.directory/'journal.sqlite3').read_bytes()
    envelope=approval(s)
    with pytest.raises(JournalError,match='journal-path-unavailable'):s.op.execute(envelope)
    assert (s.directory/'journal.sqlite3').read_bytes()==before
    assert json.loads(s.current['raw'])==asdict(s.release)
    with pytest.raises(DockerError,match='state-changed'):s.op.execute(envelope)
    s.op.current_writer=lambda raw:s.current.update(raw=raw)
    assert s.op.execute(approval(s))['status']=='succeeded'


@pytest.mark.parametrize('change', ['scope','previous','history','lkg'])
def test_ineligible_history_remains_blocked(operation,change):
    s=operation
    with s.lock() as lease, Journal(s.directory,lease) as j:
        if change=='scope':
            c=replace(s.candidate,scope='first-project-router-rollout')
            j.db.execute('UPDATE authorizations SET payload=?,candidate=? WHERE nonce=?',
                         (encode(asdict(c)),c.identity,'2'*32))
        if change=='previous':j.db.execute('UPDATE attempts SET previous=target WHERE run=?',('2'*32,))
        if change=='history':j.db.execute('UPDATE events SET reason=? WHERE run=? AND state=?',
                                        ('runtime-unverified','2'*32,'recovery-required'))
        if change=='lkg':j.db.execute('DELETE FROM good')
    with pytest.raises((DockerError,JournalError)):
        s.op.prepare()


@pytest.mark.parametrize('failure',[None,'approval','context','run','workflow','release','bootstrap'])
def test_retained_request_requires_original_crypto_and_fresh_release(operation,monkeypatch,failure):
    import base64
    from scripts.cd import update_recovery_runtime as runtime
    from scripts.cd.policy import pin
    from scripts.cd.signatures import REQUEST_NAMESPACE
    from scripts.production_integration.operator_approval import NAMESPACE
    s=operation
    document=policy_document(s.identities)
    workflow=b'reviewed immutable workflow'
    document['deployment_workflows']['DanilFaritovich/project-router']['sha256']=pin(workflow)
    policy=signed_policy(document,s.identities)
    role=policy.document['automation']['principal']
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        row=journal.db.execute('SELECT * FROM authorizations WHERE nonce=?',('2'*32,)).fetchone()
        payload={'version':1,'candidate':s.candidate.identity,'operator':role,'nonce':'2'*32,
                 'not_before':int(row['not_before']),'expires':int(row['expires'])}
        signed=json.loads(sign(payload,principal=role,key=s.identities['automation'][0],namespace=NAMESPACE))
        signed.pop('principal')
        envelope=canonical(signed)
        now=int(time.time())
        context={'approval_sha256':pin(envelope),'candidate':s.candidate.identity,
            'repository':'DanilFaritovich/project-router','source_sha':s.candidate.producer_sha,
            'run_id':55,'run_attempt':3,'not_before':now-5,'expires':now+295}
        if failure=='context':context['candidate']='sha256:'+'0'*64
        ctx=sign(context,principal=role,key=s.identities['automation'][0],namespace=REQUEST_NAMESPACE)
        request={'op':'application','request':{'op':'execute','release':asdict(s.release),
                 'candidate':asdict(s.candidate),'envelope':base64.b64encode(envelope).decode()},
                 'context':base64.b64encode(ctx).decode()}
        if failure=='approval':request['request']['envelope']=base64.b64encode(b'bad').decode()
        run={'id':55,'run_attempt':3,'path':'.github/workflows/deploy.yml',
             'head_sha':s.candidate.producer_sha,'event':'workflow_run','head_branch':'main',
             'status':'completed','conclusion':'failure',
             'repository':{'full_name':context['repository']},'head_repository':{'full_name':context['repository']}}
        if failure=='run':run['run_attempt']=4
        github=SimpleNamespace(get=lambda *a:run)
        monkeypatch.setattr(runtime,'source_file',lambda *a:b'changed' if failure=='workflow' else workflow)
        monkeypatch.setattr(Journal,'production_bootstrap',lambda *a:failure!='bootstrap')
        verified=[]
        def verify(release):
            if failure=='release':raise DockerError('cd-test-release-untrusted')
            verified.append(release)
        monkeypatch.setattr(runtime,'ImageReleaseVerifier',lambda *a:SimpleNamespace(verify=verify))
        before=(s.directory/'journal.sqlite3').read_bytes()
        def invoke():return runtime.authenticated_release(canonical(request),journal,'2'*32,
                            standing=policy,github=github,registry=None,crypto=None)
        if failure:
            with pytest.raises(DockerError):invoke()
            assert not verified
        else:
            assert invoke()==(s.release,pin(canonical(request)))
            assert verified==[s.release]
        assert (s.directory/'journal.sqlite3').read_bytes()==before


def test_delegated_ssh_cannot_select_update_recovery(monkeypatch):
    from installation import cd_forced_command
    monkeypatch.setattr(cd_forced_command.sys,'argv',['forced','operator-application-update-recovery'])
    monkeypatch.setattr(cd_forced_command.os,'execve',lambda *a:pytest.fail('delegated recovery'))
    assert cd_forced_command.main()==2


@pytest.mark.parametrize('document',[{'op':'reset'},{'op':'prepare','release':{}},
                                     {'op':'execute','envelope':'x','adapter':'arbitrary'}])
def test_root_recovery_rejects_request_selected_bindings(monkeypatch,document):
    import io
    from scripts.cd import update_recovery_runtime as runtime
    monkeypatch.setattr('os.geteuid',lambda:0)
    monkeypatch.setattr('sys.stdin',SimpleNamespace(buffer=io.BytesIO(canonical(document))))
    with pytest.raises(DockerError,match='operation-invalid'):runtime.main()


def test_late_sql_failure_retains_original_pending_state(operation,monkeypatch):
    s=operation
    original_validate=Journal.validate
    def reject_promoted(journal):
        original_validate(journal)
        if journal.db.execute('SELECT status FROM runs WHERE id=?',('2'*32,)).fetchone()[0]=='succeeded':
            raise JournalError('controlled verification failure')
    monkeypatch.setattr(Journal,'validate',reject_promoted)
    before=(s.directory/'journal.sqlite3').read_bytes()
    with pytest.raises(JournalError,match='controlled verification failure'):
        s.op.execute(approval(s))
    assert (s.directory/'journal.sqlite3').read_bytes()==before
    assert json.loads(s.current['raw'])==asdict(s.release)
    audit=next(s.op.state.glob('application-update-recovery-*'))
    assert (audit/'original.sqlite3').read_bytes()==before
    monkeypatch.setattr(Journal,'validate',original_validate)
    assert s.op.execute(approval(s))['status']=='succeeded'
