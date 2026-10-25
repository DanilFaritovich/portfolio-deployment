"""Real SSHSIG and filesystem integration; external release ports are offline doubles."""
import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from installation.operator_bootstrap import read_bundle, exclusive
from scripts.cd.policy import StandingPolicy, AutomationVerifier, pin, FROZEN_BOUNDARY
from scripts.cd.release import ImageRelease, ProjectedContent
from scripts.cd.signatures import sign, POLICY_NAMESPACE
from scripts.cd.upgrade import ManagerUpdater, verify_tree, exchange
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.candidate import hashed
from scripts.production_integration.operator_approval import canonical, NAMESPACE
from scripts.docker_adapter import DockerError
from test_production_installation import package

REPO = Path(__file__).resolve().parents[1]
BASE, NEXT = 'a'*40, 'b'*40
IMAGE = 'ghcr.io/danilfaritovich/project-router@sha256:' + 'a'*64


@pytest.fixture
def identities(tmp_path):
    keys = {}
    for role in ('operator', 'automation', 'observer'):
        path = tmp_path / role
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)], check=True)
        keys[role] = (path, ' '.join(path.with_suffix('.pub').read_text().split()[:2]))
    return keys


@pytest.fixture(autouse=True)
def disposable_directory_policy(monkeypatch, tmp_path):
    # Filesystem integration runs inside pytest's private /tmp namespace. The
    # production ancestor policy correctly refuses /tmp's world-writable parent.
    def validate(directory, *, uid):
        path = Path(directory)
        assert path.is_relative_to(tmp_path)
        assert path.stat().st_uid == uid and not path.stat().st_mode & 0o077
    monkeypatch.setattr('scripts.production_integration.gateway_write.validate_storage_directory', validate)


def policy_document(identities):
    check = {'repository': 'DanilFaritovich/portfolio-deployment', 'name': 'Offline checks', 'app_id': 15368,
             'workflow': '.github/workflows/offline.yml'}
    appcheck = {'repository': 'DanilFaritovich/project-router', 'name': 'Quality and static build', 'app_id': 15368,
                'workflow': '.github/workflows/ci.yml'}
    publication = {**appcheck, 'name': 'Publish validated image / publish'}
    return {'version': 1, 'id': '1'*32, 'host': '2'*32, 'daemon': 'daemon',
        'not_before': int(time.time()) - 10, 'expires': int(time.time()) + 86400,
        'initial_manager_sha': BASE,
        'automation': {'principal': 'automation', 'key': identities['automation'][1]},
        'observer': {'principal': 'observer', 'key': identities['observer'][1]},
        'manager': {'assets': {'vendor/library.py': pin(b'# pinned runtime'), 'tools/caddy': pin(b'not executed')},
                    'frozen': {n: 'a'*64 for n in FROZEN_BOUNDARY},
                    'checks': [check], 'workflow': '.github/workflows/manager-release.yml',
                    'workflow_sha256': 'a'*64, 'bundle_name': 'portfolio-deployment.zip'},
        'application': {'template_sha256': pin((REPO/'registry/applications/project-router.yaml').read_bytes()),
                        'workflow_sha256': 'a'*64, 'checks': [appcheck], 'publication': publication,
                        'slsa_builder': 'https://github.com/actions/build',
                        'initial_release': {'source_sha': BASE, 'image': IMAGE, 'html_sha256': 'c'*64,
                                            'run_id': '1', 'run_attempt': '1'}}, 'host_contract': {},
        'deployment_workflows': {r: {'path': '.github/workflows/deploy.yml', 'sha256': 'a'*64} for r in
                                 ('DanilFaritovich/portfolio-deployment', 'DanilFaritovich/project-router')}}


def signed_policy(doc, identities):
    raw = sign(doc, principal='operator', key=identities['operator'][0], namespace=POLICY_NAMESPACE)
    return StandingPolicy(raw, operator_keys={'operator': identities['operator'][1]})


def candidate(scope='update-project-router'):
    return OperationCandidate('2'*32, 'daemon', NEXT, BASE, IMAGE, *(['sha256:'+'a'*64]*8), scope)


def approval(c, identities, *, role='automation', nonce='5'*32, lifetime=295):
    now = int(time.time())
    payload = {'version': 1, 'candidate': c.identity, 'operator': role, 'nonce': nonce,
               'not_before': now-1, 'expires': now+lifetime}
    doc = json.loads(sign(payload, principal=role, key=identities[role][0], namespace=NAMESPACE))
    doc.pop('principal')
    return canonical(doc)


def test_real_sshsig_standing_policy_and_scoped_delegation(identities):
    policy = signed_policy(policy_document(identities), identities)
    c = candidate()
    assert AutomationVerifier(policy, c).authenticate(approval(c, identities)).operator == 'automation'
    for scope in ('first-bootstrap', 'first-project-router-rollout', 'manager-upgrade'):
        c = candidate(scope)
        with pytest.raises(DockerError, match='cd-scope-not-authorized'):
            policy.operation(c, approval(c, identities))
    with pytest.raises(DockerError):
        policy.operation(candidate(), approval(candidate(), identities, role='observer'))
    with pytest.raises(DockerError):
        policy.operation(candidate(), approval(candidate(), identities, lifetime=1000))


def test_policy_tamper_expiry_identity_separation_and_revocation(identities):
    doc = policy_document(identities)
    for mutate in (lambda d: d.update(expires=1),
                   lambda d: d['observer'].update(key=d['automation']['key'])):
        changed = json.loads(json.dumps(doc))
        mutate(changed)
        with pytest.raises(DockerError):
            signed_policy(changed, identities)
    policy = signed_policy(doc, identities)
    policy.reader = lambda: b'revoked'
    with pytest.raises(DockerError, match='revoked'):
        policy.current()


def test_release_projection_uses_complete_existing_admission(tmp_path, identities):
    installation = SimpleNamespace(manager_sha=NEXT, verify=lambda: None)
    policy = signed_policy(policy_document(identities), identities)
    release = ImageRelease(NEXT, IMAGE, 'c'*64, '3', '2')
    projected = ProjectedContent(installation, policy, release, root=REPO)
    desired, raw, admission = projected()
    original = (REPO/'registry/applications/project-router.yaml').read_bytes()
    doc = json.loads(raw)
    assert desired.applications[0].spec['components'][0]['image'] == IMAGE
    assert doc['release']['components']['web']['sourceSha'] == NEXT
    assert doc['spec']['runtimeSecurity'] if 'runtimeSecurity' in doc['spec'] else True
    assert (REPO/'registry/applications/project-router.yaml').read_bytes() == original
    assert admission == (REPO/'policy/admission.yaml').read_bytes()
    c = replace(candidate(), image=release.image, producer_sha=NEXT, policy_hash='sha256:'+pin(admission))
    assert projected.verify_source_binding(c, 'sha256:'+pin(original), c.policy_hash) == 'sha256:'+pin(raw)
    with pytest.raises(DockerError):
        projected.verify_source_binding(c, 'sha256:'+'f'*64, c.policy_hash)


def installed_tree(root, raw):
    manifest, files = read_bundle(raw)
    root.mkdir(mode=0o700)
    inventory = {'/etc/systemd/system/portfolio-deployment-executor.service': pin(files['executor.service'][0])}
    for n, (data, mode) in files.items():
        if n == 'executor.service':
            continue
        path = root / n
        for parent in reversed(path.relative_to(root).parents):
            (root / parent).mkdir(mode=0o700, exist_ok=True)
        exclusive(path, data, mode)
        inventory[str(path)] = pin(data)
    exclusive(root/'installation.json', canonical({'manager_sha': manifest['manager_sha'], 'files': sorted(map(list, inventory.items()))}))


def change_revision(raw, revision):
    import io
    import zipfile
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(target, 'w') as output:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == 'bundle.json':
                doc = json.loads(data)
                doc['manager_sha'] = revision
                data = canonical(doc)
            output.writestr(entry, data)
    return target.getvalue()


def upgrade_world(tmp_path, identities, *, health=lambda root, sha: True, swap=exchange):
    basic=read_bundle(package())[1]
    boundary={n:b'# fixed boundary' for n in FROZEN_BOUNDARY if n not in basic}
    old = package(**{**boundary,'scripts/deployment_journal.py': b'# unchanged schema',
                     'scripts/production_integration/bootstrap.py': b'# before IPAM fix'})
    new = change_revision(package(**{**boundary,'scripts/deployment_journal.py': b'# unchanged schema',
                                    'scripts/production_integration/bootstrap.py': b'# IPAM fix'}), NEXT)
    root, state = tmp_path/'manager', tmp_path/'receipts'
    installed_tree(root, old)
    state.mkdir(mode=0o700)
    doc = policy_document(identities)
    files = read_bundle(new)[1]
    doc['manager']['frozen'] = {n: pin(files[n][0]) for n in set(doc['manager']['frozen'])|
                              {n for n in files if n.startswith(('schemas/','manager/'))}}
    policy = signed_policy(doc, identities)
    class Github:
        def get(self, repo, path):
            before=path.split('/compare/',1)[1].split('...')[0] if '/compare/' in path else BASE
            return {'status': 'ahead', 'merge_base_commit': {'sha': before}, 'base_commit': {'sha': before}}
    class Verifier:
        github = Github()
        def verify(self, raw):
            return SimpleNamespace(manager_sha=read_bundle(raw)[0]['manager_sha'], bundle_sha256=pin(raw))
    @contextmanager
    def lock():
        yield object()
    installed = SimpleNamespace(manager_sha=BASE, verify=lambda: None)
    updater = ManagerUpdater(policy=policy, bundle_verifier=Verifier(), installation=installed,
        lock=lock, quiescent=lambda lease: None, health=health, root=root, state=state, swap=swap)
    return updater, new


def test_real_directory_exchange_upgrade_preserves_old_tree_and_rejects_replay(tmp_path, identities):
    updater, raw = upgrade_world(tmp_path, identities)
    old = verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())
    c = updater.prepare(raw)
    envelope = approval(c, identities)
    assert updater.run(raw, c, envelope)['status'] == 'succeeded'
    assert verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())['manager_sha'] == NEXT
    retained = updater.root.parent / ('.portfolio-deployment-'+'5'*32)
    assert verify_tree(retained, active_root=updater.root, uid=os.geteuid()) == old
    with pytest.raises(Exception):
        updater.run(raw, c, envelope)


def test_known_health_failure_restores_complete_tree(tmp_path, identities):
    updater, raw = upgrade_world(tmp_path, identities, health=lambda root, sha: sha == BASE)
    old = verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())
    c = updater.prepare(raw)
    assert updater.run(raw, c, approval(c, identities))['status'] == 'rolled-back'
    assert verify_tree(updater.root, active_root=updater.root, uid=os.geteuid()) == old


def test_interruption_fences_new_requests_until_operator_restoration(tmp_path, identities):
    def crash(root, sha):
        raise TimeoutError('unknown result')
    updater, raw = upgrade_world(tmp_path, identities, health=crash)
    c = updater.prepare(raw)
    with pytest.raises(TimeoutError):
        updater.run(raw, c, approval(c, identities))
    with pytest.raises(DockerError, match='recovery-required'):
        updater.prepare(raw)
    updater.health = lambda root, sha: True
    assert updater.recover('5'*32, approval(c, identities, role='operator', nonce='6'*32))['status'] == 'rolled-back'
    assert verify_tree(updater.root, active_root=updater.root, uid=os.geteuid())['manager_sha'] == BASE


def test_updater_rejects_frozen_change_before_staging(tmp_path, identities):
    updater, raw = upgrade_world(tmp_path, identities)
    doc = json.loads(updater.policy.payload_bytes)
    doc['manager']['frozen']['bindings.py'] = 'f'*64
    updater.policy = signed_policy(doc, identities)
    with pytest.raises(DockerError, match='frozen'):
        updater.prepare(raw)
    assert not list(updater.state.iterdir())


def test_already_installed_manager_is_noop_without_tree_or_journal_changes(tmp_path, identities):
    updater, raw = upgrade_world(tmp_path, identities)
    c = updater.prepare(raw)
    updater.run(raw,c,approval(c,identities))
    updater.installation.manager_sha = NEXT
    before = verify_tree(updater.root,active_root=updater.root,uid=os.geteuid())
    same = updater.prepare(raw)
    assert updater.run(raw,same,approval(same,identities,nonce='7'*32))['status'] == 'no-op'
    assert verify_tree(updater.root,active_root=updater.root,uid=os.geteuid()) == before


@pytest.mark.parametrize('failure', [None,'workflow','attempt','branch','event','source','signature','envelope'])
def test_deployment_context_is_bound_to_reviewed_workflow_and_attempt(monkeypatch,identities,failure):
    from scripts.cd.context import verify_context
    from scripts.cd.signatures import REQUEST_NAMESPACE
    workflow = b'name: reviewed deploy\n'
    doc = policy_document(identities)
    repo = 'DanilFaritovich/portfolio-deployment'
    doc['deployment_workflows'][repo]['sha256'] = pin(workflow)
    policy = signed_policy(doc,identities)
    c = candidate('manager-upgrade')
    envelope = approval(c,identities)
    payload = {'approval_sha256':pin(envelope),'candidate':c.identity,'repository':repo,'source_sha':NEXT,
               'run_id':10,'run_attempt':1,'not_before':int(time.time())-1,'expires':int(time.time())+100}
    run = {'id':10,'run_attempt':1,'path':'.github/workflows/deploy.yml','event':'workflow_run',
           'head_branch':'main','head_sha':NEXT,'repository':{'full_name':repo},
           'head_repository':{'full_name':repo},'status':'in_progress'}
    if failure == 'workflow': run['path'] = '.github/workflows/untrusted.yml'
    if failure == 'attempt': run['run_attempt'] = 2
    if failure == 'branch': run['head_branch'] = 'develop'
    if failure == 'event': run['event'] = 'pull_request'
    if failure == 'source': run['head_sha'] = BASE
    if failure == 'envelope': payload['approval_sha256'] = 'f'*64
    raw = sign(payload,principal='automation',key=identities['automation'][0],namespace=REQUEST_NAMESPACE)
    if failure == 'signature':
        raw = sign(payload,principal='observer',key=identities['observer'][0],namespace=REQUEST_NAMESPACE)
    class Github:
        def get(self,repo,path):
            if path.startswith('/contents/'):
                return {'type':'file','path':'.github/workflows/deploy.yml','encoding':'base64',
                        'size':len(workflow),'content':base64.b64encode(workflow).decode()}
            return run
    monkeypatch.setattr('scripts.cd.context.ProtectedSource',lambda *a,**k:SimpleNamespace(collect=lambda *a:None))
    if failure:
        with pytest.raises(DockerError):verify_context(raw,envelope,c,policy=policy,github=Github(),kind='manager')
    else:
        assert verify_context(raw,envelope,c,policy=policy,github=Github(),kind='manager') == payload


def test_forced_ssh_command_rejects_shell_and_selected_roles(monkeypatch):
    from installation import cd_forced_command as forced
    calls = []
    monkeypatch.setattr(forced.os,'execve',lambda *args:calls.append(args))
    monkeypatch.setattr(forced.sys,'argv',['forced','automation'])
    for command in ('sh','sudo docker ps','portfolio-cd-v1; id',''):
        monkeypatch.setenv('SSH_ORIGINAL_COMMAND',command)
        assert forced.main() == 2
    assert not calls
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND','portfolio-cd-v1')
    forced.main()
    assert calls[0][0] == '/usr/bin/sudo'
    assert calls[0][1][-1] == 'automation'
    assert 'DOCKER_HOST' not in calls[0][2]


def test_observer_never_obtains_automation_or_operator_authority(monkeypatch,identities):
    from scripts.cd import bridge
    policy = signed_policy(policy_document(identities),identities)
    monkeypatch.setattr(bridge,'installation',lambda:object())
    monkeypatch.setattr(bridge,'installed_policy',lambda i:policy)
    for op in ('application','manager-prepare','manager-execute','recover','sh'):
        with pytest.raises(DockerError,match='observer-scope'):
            bridge.handle('observer',{'op':op},None)


def test_manager_upgrade_shares_real_flock_and_preserves_inode(tmp_path,identities):
    from scripts.deployment import host_lock
    updater,raw=upgrade_world(tmp_path,identities)
    updater.lock=lambda:host_lock(tmp_path/'locks')
    c=updater.prepare(raw)
    with host_lock(tmp_path/'locks'):
        inode=(tmp_path/'locks/host.lock').stat().st_ino
        with pytest.raises(Exception):
            updater.run(raw,c,approval(c,identities))
        assert not list(updater.state.iterdir())
    assert updater.run(raw,c,approval(c,identities))['status']=='succeeded'
    assert (tmp_path/'locks/host.lock').stat().st_ino==inode


@pytest.mark.parametrize('failure',[None,'signature','workflow','pending','attempt'])
def test_manager_release_requires_completed_publication_and_exact_attested_bytes(monkeypatch,identities,failure):
    from scripts.cd.manager_release import ManagerReleaseVerifier
    workflow=b'name: protected manager release\n'
    doc=policy_document(identities)
    doc['manager']['workflow_sha256']=pin(workflow)
    policy=signed_policy(doc,identities)
    raw=package()
    proof=SimpleNamespace(manager_sha=BASE,bundle_sha256=pin(raw))
    monkeypatch.setattr('scripts.cd.manager_release.ManagerBundleVerifier',
        lambda **kwargs:SimpleNamespace(verify=lambda bundle:proof))
    run={'id':123,'run_attempt':2,'path':'.github/workflows/manager-release.yml','event':'push',
         'head_sha':BASE,'head_branch':'main','repository':{'full_name':'DanilFaritovich/portfolio-deployment'},
         'head_repository':{'full_name':'DanilFaritovich/portfolio-deployment'},'status':'completed','conclusion':'success'}
    if failure=='workflow':run['path']='.github/workflows/evil.yml'
    if failure=='pending':run['status']='in_progress'
    class Github:
        def get(self,repo,path):
            if path.startswith('/contents/'):
                return {'type':'file','path':'.github/workflows/manager-release.yml','encoding':'base64',
                        'size':len(workflow),'content':base64.b64encode(workflow).decode()}
            if path.startswith('/actions/runs?'):return {'total_count':1,'workflow_runs':[run]}
            assert path=='/attestations/sha256:'+pin(raw)+'?per_page=100'
            return {'attestations':[{'bundle':{'untrusted':'input'}}]}
    class Crypto:
        def verify(self,subject,image,identity,expectations,bundles):
            assert subject==raw and image=='portfolio-deployment.zip@sha256:'+pin(raw)
            assert identity['signerSha']==BASE
            assert expectations[0]['expectedInvocation'].endswith('/123/attempts/2')
            if failure in ('signature','attempt'):raise DockerError('production-release-signature-unverified')
            return ({'predicateType':'https://slsa.dev/provenance/v1',
                     'subject':[{'name':'portfolio-deployment.zip','digest':{'sha256':pin(raw)}}]},)
    verifier=ManagerReleaseVerifier(policy,Github(),Crypto())
    if failure:
        with pytest.raises(DockerError):verifier.verify(raw)
    else:assert verifier.verify(raw)==proof


def test_observer_accepts_pinned_ca_bundle_larger_than_request_header(tmp_path,monkeypatch):
    from scripts.cd import bridge
    challenge={'policy_id':'sha256:'+'a'*64,'created':int(time.time()),'ca':'A'*300000}
    (tmp_path/'challenge.json').write_bytes(canonical(challenge))
    monkeypatch.setattr(bridge,'STATE',tmp_path)
    monkeypatch.setattr(bridge,'protected',lambda path:None)
    assert bridge.observe(SimpleNamespace(identity=challenge['policy_id']))==challenge


@pytest.mark.parametrize('status,base,accepted',[('ahead',BASE,True),('behind',BASE,False),('diverged',BASE,False),('ahead',NEXT,False)])
def test_delayed_workflow_cannot_downgrade_application_source(status,base,accepted):
    from scripts.cd.release import require_forward_release
    previous=ImageRelease(BASE,IMAGE,'c'*64,'1','1')
    target=replace(previous,source_sha=NEXT)
    github=SimpleNamespace(get=lambda repo,path:{'status':status,'base_commit':{'sha':BASE},'merge_base_commit':{'sha':base}})
    if accepted:assert require_forward_release(previous,target,github)==target
    else:
        with pytest.raises(DockerError,match='source-downgrade'):
            require_forward_release(previous,target,github)
