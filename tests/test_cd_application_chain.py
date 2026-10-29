"""Offline automatic CD path with real handler, composition, preflight and journal.

The installed binding factory constructs the real executor using controlled
Engine, release-authority and host-source ports. No SSH, Docker or network runs.
"""
import base64
import copy
from dataclasses import asdict, fields, replace
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest
import yaml

from scripts.cd import application, runner
from scripts.cd.release import ImageRelease, ProjectedContent
from scripts.cd.signatures import sign
from scripts.compose import render_compose
from scripts.deployment_journal import Journal, Intent
from scripts.deployment import owns_host_lock
from scripts.deployment_lifecycle import runtime_identity
from scripts.docker_adapter import DockerError
from scripts.production_integration import composition, image_preparation, public_postconditions, public_witness
from scripts.production_integration.artifacts import blob_digest, verify_oci_bytes, bind_engine_image, SourceVerification
from scripts.production_integration.candidate import hashed
from scripts.production_integration.fixed_bindings import FixedPolicy
from scripts.production_integration.host_policy import PolicyHostContracts
from scripts.production_integration.local_sources import PATHS
from scripts.production_integration.operator_approval import canonical, NAMESPACE
from scripts.production_integration.public_postconditions import PublicPolicy
from scripts.reconciliation import encode
from test_cd import identities as identity_keys, policy_document, signed_policy
from test_operator_approval import signer, verifier
from test_production_contracts import registration
from test_production_execution import setup
from test_production_integration import router, proxy, state
from test_readiness_recovery import Clock, POLICY, VERIFIED
from test_trusted_bindings import integrated, fixed_composition, CID, NID


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    real_process = subprocess.Popen
    def process(argv, *args, **kwargs):
        assert Path(argv[0]).name == 'ssh-keygen', 'non-crypto subprocess forbidden'
        return real_process(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', process)
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network forbidden'))


@pytest.fixture
def identities(tmp_path):
    directory = tmp_path/'identities'
    directory.mkdir()
    return identity_keys.__wrapped__(directory)


@pytest.fixture
def chain(integrated, fixed_composition, identities, tmp_path, monkeypatch):
    s = integrated
    installation = s.executor.verifier.installation
    s.world.present = s.world.route = True
    old_app, old_evidence = s.world.app, s.world.image
    old_ref = old_evidence.reference
    old_blobs = copy.deepcopy(s.blobs)
    config = json.loads(old_blobs[old_evidence.image_id])
    config['config']['Env'] = ['RELEASE=next']
    raw_config = encode(config).encode()
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                   'digest': blob_digest(raw_config), 'size': len(raw_config)}, 'layers': []}
    raw_manifest = encode(manifest).encode()
    target_ref = 'ghcr.io/danilfaritovich/project-router@' + blob_digest(raw_manifest)
    blobs = {old_ref: old_blobs, target_ref: {blob_digest(raw_config):raw_config,
                                             blob_digest(raw_manifest):raw_manifest}}
    evidence = {ref:verify_oci_bytes(ref, ref.split('@')[0], raw) for ref,raw in blobs.items()}
    old_release = ImageRelease('b'*40,old_ref,hashlib.sha256(b'old').hexdigest(),'1','1')
    target = ImageRelease('d'*40,target_ref,hashlib.sha256(b'next').hexdigest(),'2','1')
    s.cached, s.operations, s.public_checks, s.results = {old_ref}, [], [], []
    s.fail = None
    s.current_ref = old_ref
    original_inspect = s.engine.inspect
    def inspect(kind, identity):
        if kind != 'image': return original_inspect(kind, identity)
        if identity not in s.cached: raise DockerError('docker-command-failed')
        item = evidence[identity]
        return {'Id':item.image_id,'RepoDigests':[identity],'Os':'linux','Architecture':'amd64',
                'Config':json.loads(item.defaults_json)}
    s.engine.inspect = inspect
    def listing(args):
        assert args == image_preparation.LIST
        return b'\n'.join(encode({'Repository':r.split('@')[0], 'Digest':r.split('@')[1],
                               'ID':evidence[r].image_id}).encode() for r in sorted(s.cached))
    s.engine._run = listing
    def pull(argv, **kwargs):
        assert argv[:7] == ['/usr/bin/docker','--host','unix:///run/docker.sock','image','pull','--platform','linux/amd64']
        assert argv[7] == target_ref
        s.operations.append('pull')
        if s.fail=='pull-unknown':raise DockerError('docker-timeout',True)
        s.cached.add(target_ref)
    monkeypatch.setattr(image_preparation,'run_bounded',pull)
    store = tmp_path/'cd-state'; store.mkdir(mode=0o700)
    ca = tmp_path/'ca.pem'; ca.write_bytes(b'synthetic CA')
    public = PublicPolicy(s.engine.identity(),('8.8.8.8',),(),'1.1.1.1',str(ca),
                          hashlib.sha256(ca.read_bytes()).hexdigest(),old_release.html_sha256)
    fixed = FixedPolicy(**{f.name:None for f in fields(FixedPolicy)})
    fixed = replace(fixed,public=public)
    _, arguments = fixed_composition
    contents = tuple({'path':p,'uid':0,'gid':0,'mode':0o700,'device':1,'inode':n}
                     for n,p in enumerate(PATHS,1))
    caddyfile = tmp_path/'Caddyfile';caddyfile.write_bytes(b'synthetic active gateway')
    doc = policy_document(identities)
    doc['host'],doc['daemon'] = s.engine.identity()
    doc['application']['initial_release'] = asdict(old_release)
    doc['host_contract'] = {'host_policy':asdict(s.policy),'kernel_sha256':hashed(s.facts.network),
        'storage_roots':{r['path']:{k:r[k] for k in ('uid','gid','mode','device','inode')} for r in contents},
        'caddyfile_sha256':hashlib.sha256(caddyfile.read_bytes()).hexdigest(),
        'caddy_runtime_hash':'a'*64,'caddy_evidence_hash':'b'*64,
        'rollout':{'infrastructure':tuple(s.inventory.infrastructure)}}
    # Real projected content/admission over a private copy of the registered template.
    repo = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((repo/'registry/applications/project-router.yaml').read_text())
    document['spec'] = old_app.spec
    document['spec']['components'][0].pop('proxyNetwork',None)
    manager = tmp_path/'package'/'manager'
    (manager/'registry/applications').mkdir(parents=True)
    (manager/'policy').mkdir()
    raw = yaml.safe_dump(document).encode()
    (manager/'registry/applications/project-router.yaml').write_bytes(raw)
    (manager/'policy/admission.yaml').write_bytes((repo/'policy/admission.yaml').read_bytes())
    doc['application']['template_sha256'] = hashlib.sha256(raw).hexdigest()
    standing = signed_policy(doc,identities)
    real_content = ProjectedContent
    monkeypatch.setattr(application,'ProjectedContent',lambda i,p,r:real_content(i,p,r,root=manager))
    desired_old = real_content(installation,standing,old_release,root=manager)()[0]
    desired_target = real_content(installation,standing,target,root=manager)()[0]
    # Runtime uses the exact normalized contract admitted by ProjectedContent.
    old_app = desired_old.applications[0]
    def runtime(ref):
        app = old_app if ref==old_ref else desired_target.applications[0]
        _, container, _ = state(app)
        f = container.facts
        f['config'] = {**json.loads(evidence[ref].defaults_json),'Image':ref}
        f['imageId'] = evidence[ref].image_id
        s.world.app = desired_target.applications[0]
        s.world.desired = desired_target
        s.world.container = replace(container,facts_json=encode(f))
        s.world.image = evidence[ref]
        s.current_ref = ref
    runtime(old_ref)
    monkeypatch.setattr(application,'CURRENT',store/'current-release.json')
    monkeypatch.setattr(application,'STATE',store)
    monkeypatch.setattr(application,'installed_policy',lambda i:standing)
    monkeypatch.setattr(application,'protected',lambda p:None)
    monkeypatch.setattr(application,'production_host_lock',s.lock)
    monkeypatch.setattr(application,'require_production_lease',lambda lease:None)
    monkeypatch.setattr(application,'installed_engine',lambda *a:s.engine)
    monkeypatch.setattr(application,'release_ports',lambda *a:(None,None,None,None))
    # Release authenticity is an independent authority port, covered by collector tests.
    monkeypatch.setattr(application.ImageReleaseVerifier,'verify',lambda self,r:r.validate())
    monkeypatch.setattr(application,'require_forward_release',lambda previous,target,g:target)
    monkeypatch.setattr(application,'HTTPSRead',lambda **kw:None)
    class Registry:
        def __init__(self,*a,**kw):pass
        def collect(self,ref):
            result=copy.deepcopy(blobs[ref])
            if s.fail=='oci' and ref==target_ref:
                key=next(iter(result));result[key]+=b'corrupted'
            return result
    monkeypatch.setattr(application,'GHCRRead',Registry)
    monkeypatch.setitem(sys.modules,'bindings',SimpleNamespace(POLICY=fixed))
    from scripts.cd import host
    monkeypatch.setattr(host,'Path',lambda path:caddyfile)
    monkeypatch.setattr(composition,'installed_engine',lambda *a:s.engine)
    monkeypatch.setattr(composition,'InstalledConfigAdapter',lambda *a:s.ports['gateway'].port.transport.adapter)
    transport = s.ports['gateway'].port.transport
    monkeypatch.setattr(composition,'installed_active_channel',lambda *a,**kw:(transport.channel,transport.runtime))
    real_http = composition.InternalHTTPRead
    monkeypatch.setattr(composition,'InternalHTTPRead',lambda subnet,endpoints:real_http(subnet,endpoints,
        exchange=lambda *a:503 if s.fail=='readiness' and s.current_ref==target_ref else 200))
    monkeypatch.setattr(composition,'LocalHostSources',lambda *a,**kw:
        SimpleNamespace(collect=lambda before_observation:(before_observation(),s.facts)[1]))
    real_host = PolicyHostContracts
    monkeypatch.setattr(composition,'PolicyHostContracts',lambda *a,**kw:
        real_host(*a,**kw,contents=lambda:contents,clock=lambda:0))
    monkeypatch.setattr(composition,'HTTPSRead',lambda **kw:None)
    monkeypatch.setattr(composition,'GHCRRead',lambda *a,**kw:Registry())
    from scripts.production_integration import gateway_write
    monkeypatch.setattr(gateway_write,'validate_storage_directory',lambda path,uid:None)
    from scripts.production_integration.gateway import ProductionGateway
    def require_test_lease(lease):
        assert owns_host_lock(lease)
    monkeypatch.setattr(gateway_write,'ProductionGateway',
        lambda port:ProductionGateway(port,lease_verifier=require_test_lease))
    # Controlled installed binding factory: real composition.build and executor.
    def installed_factory(i,p,**kwargs):
        args = dict(arguments)
        args.update(contracts=kwargs['contracts'],content_reader=kwargs['content_reader'],
            resolve_image=kwargs['image_resolver'],host_policy=kwargs['review'].policy,
            active_channel=transport.channel,gateway_runtime=transport.runtime,caddy_image=old_evidence,
            prerequisites=kwargs['review'],image=target_ref,producer_sha=target.source_sha,
            infrastructure=tuple(tuple(r) for r in kwargs['review'].document['rollout']['infrastructure']),
            image_registry=lambda:Registry(),gateway_postconditions=kwargs['gateway_postconditions'])
        args.update(kwargs['composition_options'])
        args['lifecycle_options'] = {**args['lifecycle_options'],'policy':POLICY,
                                    'clock':clock.now,'sleep':clock.sleep}
        ex = composition.build(i,**args)
        ex.directory,ex.clock = s.directory,time.time
        ex.preflight.clock = ex.preflight.contracts.clock = lambda:0
        provider = ex.preflight.provider
        for item in (provider,provider.reader,*provider.reader.ports.values(),
                     provider.reader.ports['gateway'].port.transport):
            item.clock=lambda:0
        ex.release_factory=lambda c:SimpleNamespace(verify=lambda request:SourceVerification(request,(),hashed(asdict(c))))
        def adapter(grant):
            class Adapter:
                test_session=None
                def authorize(self,artifact):grant.require()
                def up(self,artifact,service):
                    grant.require();s.operations.append('up')
                    ref=artifact.model['services'][service]['image']
                    assert ref in (old_ref,target_ref)
                    runtime(ref)
                    if s.fail=='mutation-unknown' and ref==target_ref:raise DockerError('docker-timeout',True)
            return Adapter()
        ex.adapter_factory=adapter
        return ex
    clock = Clock()
    monkeypatch.setattr(application,'build_fixed',installed_factory)
    # Real public policy/DNS/HTTP acceptance and SSHSIG witness, synthetic I/O.
    receipt=store/'observation.json'
    monkeypatch.setattr(public_witness,'RECEIPT',receipt)
    monkeypatch.setattr(public_witness,'protected',lambda p:None)
    def witness():
        challenge=json.loads((store/'challenge.json').read_bytes())
        subject={'origin':'https://danil-miftakhov.dev','path':'/en/',
            'identity':challenge['policy']['identity'],'routes':challenge['routes'],
            'policy':hashed(challenge['policy'])}
        if s.fail=='witness' and s.current_ref==target_ref:return
        if s.fail=='rollback-witness':return
        now=int(time.time())
        signed=json.loads(sign({'subject':subject,'observed_at':now,'expires':now+30},
            principal='public-observer',key=identities['observer'][0],namespace=public_witness.NAMESPACE))
        signed.pop('principal');receipt.write_bytes(canonical(signed))
    class PublicNetwork:
        def resolve(self,resolver,kind,deadline):return ('8.8.8.8',) if kind==1 else ()
        def https(self,address,ca,deadline):
            witness();s.public_checks.append(s.current_ref)
            body=b'old' if s.current_ref==old_ref else b'next'
            if s.fail=='public-body' and s.current_ref==target_ref:body=b'wrong release'
            return b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
    monkeypatch.setattr(public_postconditions,'PublicNetwork',PublicNetwork)
    # Establish the already-running application as LKG in the real private journal.
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        journal.prepare('a'*32,'project-router',installation.manager_sha,
            (Intent('web',None,old_ref,runtime_identity(old_app)),))
        journal.transition('a'*32,'web','verifying','mutation-completed')
        journal.transition('a'*32,'web','succeeded','readiness-verified',VERIFIED)
        journal.promote('a'*32)
    monkeypatch.setenv('CD_AUTOMATION_PRINCIPAL','automation')
    client=runner.Client.__new__(runner.Client)
    client.keys={'automation':identities['automation'][0]}
    def call(request,**kwargs):
        assert request['op']=='application'
        result=application.handle(installation,request['request'])
        s.results.append(result)
        if result['status'] in ('blocked','recovery-required'):raise DockerError(encode(result))
        return result
    client.call=call
    client.context=lambda *a:'controlled-SSH-context'
    directory=tmp_path/'evidence';directory.mkdir()
    (directory/'mapping.json').write_text(json.dumps({'image':target_ref}))
    (directory/'public-content.json').write_text(json.dumps({'sourceSha':target.source_sha,
        'sourceRef':'refs/heads/main','sourceRepository':'DanilFaritovich/project-router',
        'imageDigest':target_ref.split('@')[1],'htmlSha256':target.html_sha256,
        'runId':target.run_id,'runAttempt':target.run_attempt}))
    s.client,s.evidence,s.target,s.old,s.store=client,directory,target,old_release,store
    return s


def test_automatic_application_update_and_repeat_noop(chain):
    s=chain
    assert runner.deploy(s.client,'application',s.evidence)['status']=='succeeded'
    assert s.operations==['pull','up']
    assert s.public_checks and s.public_checks[-1]==s.target.image
    assert json.loads((s.store/'current-release.json').read_bytes())==asdict(s.target)
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        assert journal.last_good('project-router','web')['target']==s.target.image
        assert not journal.incomplete() and not journal.authorization_recovery()
    before=list(s.operations)
    journal_before=hashlib.sha256((s.directory/'journal.sqlite3').read_bytes()).hexdigest()
    lock_inode=(s.lock_path/'host.lock').stat().st_ino
    current_before=(s.store/'current-release.json').read_bytes()
    assert runner.deploy(s.client,'application',s.evidence)['status']=='no-op'
    assert s.operations==before
    assert hashlib.sha256((s.directory/'journal.sqlite3').read_bytes()).hexdigest()==journal_before
    assert (s.lock_path/'host.lock').stat().st_ino==lock_inode
    assert (s.store/'current-release.json').read_bytes()==current_before


@pytest.mark.parametrize('outages', [1, 3, 100])
def test_update_waits_for_http_listener_before_accepting(chain, monkeypatch, outages):
    from scripts.production_integration.http_read import InternalHTTPRead
    s = chain
    original = InternalHTTPRead.get
    attempts = []
    def starting(http, *args, **kwargs):
        if s.current_ref == s.target.image:
            attempts.append(None)
            if len(attempts) <= outages:
                raise DockerError('production-http-read-unavailable')
        return original(http, *args, **kwargs)
    monkeypatch.setattr(InternalHTTPRead, 'get', starting)
    if outages == 100:
        with pytest.raises(DockerError):
            runner.deploy(s.client, 'application', s.evidence)
        assert len(attempts) < outages
        assert s.operations == ['pull', 'up', 'up']
        assert s.current_ref == s.old.image
        assert not (s.store / 'current-release.json').exists()
        with s.lock() as lease, Journal(s.directory, lease) as journal:
            assert not journal.incomplete()
            assert journal.last_good('project-router', 'web')['target'] == s.old.image
        return
    assert runner.deploy(s.client, 'application', s.evidence)['status'] == 'succeeded'
    assert len(attempts) > outages
    assert s.operations == ['pull', 'up']
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert not journal.incomplete()
        assert journal.last_good('project-router', 'web')['target'] == s.target.image


@pytest.mark.parametrize('failure',['readiness','witness','public-body'])
def test_automatic_update_rejects_failed_release_and_verifies_rollback(chain,failure):
    s=chain;s.fail=failure
    with pytest.raises(DockerError):runner.deploy(s.client,'application',s.evidence)
    assert s.current_ref==s.old.image
    assert s.operations==['pull','up','up']
    assert s.public_checks and s.public_checks[-1]==s.old.image
    assert not (s.store/'current-release.json').exists()
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        assert journal.last_good('project-router','web')['target']==s.old.image
        assert not journal.incomplete() and not journal.authorization_recovery()


@pytest.mark.parametrize('failure',['pull-unknown','mutation-unknown','rollback-witness'])
def test_unknown_outcome_or_unverified_rollback_blocks_further_cd(chain,failure):
    s=chain;s.fail=failure
    with pytest.raises(DockerError):runner.deploy(s.client,'application',s.evidence)
    assert not (s.store/'current-release.json').exists()
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        assert journal.last_good('project-router','web')['target']==s.old.image
        assert journal.incomplete() or journal.authorization_recovery()
    operations=list(s.operations)
    with pytest.raises(DockerError):runner.deploy(s.client,'application',s.evidence)
    assert s.operations==operations


@pytest.mark.parametrize('failure',['oci','approval'])
def test_invalid_oci_or_signature_rejected_before_runtime_mutation(chain,failure):
    s=chain
    if failure=='oci':s.fail='oci'
    else:s.client.approval=lambda candidate:base64.b64encode(b'not a signed approval').decode()
    with pytest.raises(DockerError):runner.deploy(s.client,'application',s.evidence)
    assert not s.operations
    assert not (s.store/'current-release.json').exists()
    with s.lock() as lease,Journal(s.directory,lease) as journal:
        assert journal.last_good('project-router','web')['target']==s.old.image
        assert not journal.incomplete() and not journal.authorization_recovery()


@pytest.mark.parametrize('worker, probe, expected', [
    (False, {'running':True,'http_status':200}, 'healthy'),
    (False, {'running':True,'http_status':503}, 'degraded'),
    (False, {'running':True,'http_status':302,'redirected':True}, 'degraded'),
    (False, {'running':True}, 'unknown'),
    (False, {'running':None}, 'unknown'),
    (False, {'running':False}, 'degraded'),
    (True, {'running':True,'health':'healthy'}, 'healthy'),
    (True, {'running':True,'health':'unhealthy'}, 'degraded'),
    (True, {'running':True,'health':'starting'}, 'degraded'),
    (True, {'running':True,'health':'unknown'}, 'unknown'),
])
def test_readiness_facts_distinguish_known_failure_from_unknown(worker,probe,expected):
    from scripts.readiness import ProbeResult
    from scripts.production_integration.runtime_sources import ReadinessSource
    engine=SimpleNamespace(identity=lambda:('a'*32,'daemon'))
    adapter=SimpleNamespace(probe=lambda *a,**kw:ProbeResult(**probe))
    source=ReadinessSource(SimpleNamespace(engine=engine),{'project-router':adapter},clock=lambda:0)
    selected=((SimpleNamespace(id='project-router'),
        (({'type':'worker' if worker else 'frontend'},SimpleNamespace(identifier='container'),None),)),)
    result=source.collect_selected('round',engine.identity(),selected)
    assert result.payload=={'container':expected}


@pytest.mark.parametrize('changed_fact', ['routes4', 'rules4', 'listeners'])
def test_kernel_policy_drift_survives_application_entry_without_mutation(chain, monkeypatch, capsys, changed_fact):
    import io
    from scripts.cd.bridge import application_result
    from scripts.production_integration.operator_approval import closed_json

    s = chain
    journal = s.directory / 'journal.sqlite3'
    before = journal.read_bytes()
    # The policy was genuinely signed over the original facts by the fixture.
    # No policy replacement/normalization or trust bypass is allowed on drift.
    row = ({'protocol': 'tcp', 'address': '127.0.0.1:12345', 'port': 12345}
           if changed_fact == 'listeners' else {'dst': '192.0.2.0/24', 'dev': 'foreign'})
    s.facts.network[changed_fact].append(row)
    request = {'op': 'prepare-cache', 'release': asdict(s.target)}
    monkeypatch.setattr(application.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(canonical(request))))
    result_code = application.main(s.executor.verifier.installation)
    raw = capsys.readouterr().out.encode()
    result = closed_json(raw.strip(), 131072)
    assert result_code == 2
    assert result == {'status': 'blocked', 'reason': 'cd-installed-entry-failed',
                      'diagnostic': 'cd-kernel-policy-drift'}
    assert application_result(SimpleNamespace(returncode=result_code, stdout=raw)) == result
    assert s.operations == [] and s.public_checks == [] and s.results == []
    assert journal.read_bytes() == before
    assert not (s.store / 'current-release.json').exists()
    assert not (s.store / 'challenge.json').exists()
