"""Synthetic Stage09.3 integration; no live ports, registry downloads or SSH."""
from dataclasses import asdict, replace
import copy
import json
from pathlib import Path
import socket
import subprocess

import pytest
import yaml

from scripts.compose import LABEL, render_compose
from scripts.deployment import preflight_resources, execute, LockLease
from scripts.deployment_journal import locked_journal
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import DockerObservation, ImageEvidence, container_runtime
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.production_integration.artifacts import (SourceRequest, SourceVerification, blob_digest, verify_artifact, bind_engine_image)
from scripts.production_integration.docker import EngineContract, ProductionDockerAdapter
from scripts.production_integration.gateway import ProductionGateway, RehearsalLifecycle, content
from scripts.production_integration.cli import main, validate_contract
from scripts.production_integration.observations import ProductionStateReader, SourceResult
from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths, ProxyContract, validate_proxy
from scripts.reconciliation import DesiredState, build_reconciliation_plan, encode, PlanningInputError
from scripts.runtime_security import projection
from scripts.validate_registry import RegistryValidationError
from test_production_contracts import registration, write, REVISION
from test_deployment import resource, docker_state
from test_readiness_recovery import World, Clock, Probes, POLICY
from test_planning import root, put, observation, empty


@pytest.fixture
def router(registration):
    root, doc = registration
    doc['spec']['enabled'] = True  # tmp registry only
    write(root, doc)
    return build_desired_state(root, REVISION).applications[0]


def state(app):
    artifact = render_compose(app)
    c = artifact.model['services']['web']
    tmpfs = dict(t.split(':', 1) for t in c['tmpfs'])
    image = ImageEvidence(c['image'], 'sha256:' + 'c'*64, 'linux/amd64', c['image'].split('@')[1], True,
                          encode({'User': '101:101', 'Healthcheck': {'Test': ['CMD', '/check']}}))
    r = resource('container', artifact.project+'-web-1', {**c['labels'],
            'com.docker.compose.project': artifact.project, 'com.docker.compose.service': 'web'},
            config={'Image': c['image'], 'User': '101:101', 'Healthcheck': {'Test': ['CMD', '/check']}},
            imageId=image.image_id, state={'Status': 'running'},
            host={'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                  'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}, 'Tmpfs': tmpfs},
            networks={artifact.model['networks'][n]['name']: {'Aliases': ['web', *o.get('aliases', [])]}
                      for n, o in c['networks'].items()},
            mounts=[{'Type': 'tmpfs', 'Destination': '/tmp', 'RW': True}])
    return artifact, r, image


def test_projection_and_security_drift(router):
    a, r, image = state(router)
    c = a.model['services']['web']
    assert c['read_only'] is True and c['user'] == '101:101'
    assert c['tmpfs'] == ['/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777']
    assert c['networks']['proxy']['aliases'] == ['project-router-web']
    assert not {'ports','privileged','environment','healthcheck'} & c.keys()
    assert container_runtime(r, a, 'web', image)
    before = load_observed_state(observation(DesiredState(REVISION, (router,))))
    spec = router.spec
    spec['components'][0]['runtimeSecurity']['readOnlyRootFilesystem'] = False
    new = replace(router, spec_json=encode(spec))
    plan = build_reconciliation_plan(DesiredState(REVISION, (new,)), before)
    assert any(e['action'] == 'update-component' for e in plan.document['entries'])


@pytest.mark.parametrize('key,value', [('privileged', True), ('ports', ['80:80']), ('capAdd',['SYS_ADMIN']),
 ('capDrop', []), ('noNewPrivileges',False), ('user','0:0'), ('user','101:0'),
 ('tmpfs',[{'target':'/var/run','sizeBytes':1048576,'mode':'1777'}]),
 ('tmpfs',[{'target':'/tmp','sizeBytes':0,'mode':'1777'}]),
 ('tmpfs',[{'target':'/tmp','sizeBytes':True,'mode':'1777'}]),
 ('tmpfs',[{'target':'/tmp','sizeBytes':1073741824,'mode':'1777'}]), ('volumes',['/var/run/docker.sock:/socket']),
 ('restart','always'), ('imageHealthcheck','disable'), ('apiVersion','unknown')])
def test_closed_security_rejects(registration, key, value):
    root, doc = registration
    doc['spec']['components'][0]['runtimeSecurity'][key] = value
    write(root, doc)
    with pytest.raises(RegistryValidationError):
        build_desired_state(root, REVISION)
    with pytest.raises(PlanningInputError):
        projection(doc['spec']['components'][0]['runtimeSecurity'])


@pytest.mark.parametrize('key,value', [('Privileged', True), ('PortBindings', {'80/tcp': []}),
 ('CapAdd',['SYS_ADMIN']), ('CapDrop',[]), ('SecurityOpt',[]), ('ReadonlyRootfs', False),
 ('Binds',['/var/run/docker.sock:/socket']), ('Tmpfs',{}),
 ('Tmpfs',{'/tmp':'rw,size=0'}), ('RestartPolicy',{'Name':'always'}), ('NetworkMode','host')])
def test_runtime_effective_fail_closed(router, key, value):
    a, r, image = state(router)
    facts = r.facts
    facts['host'][key] = value
    assert not container_runtime(replace(r, facts_json=encode(facts)), a, 'web', image)


def test_producer_alias_and_socket_mount_rejected(router):
    a, r, image = state(router)
    for change in ('alias','socket','uid'):
        f = r.facts
        if change == 'alias':
            f['networks']['portfolio-proxy']['Aliases'] = ['project-router']
        elif change == 'socket':
            f['mounts'].append({'Type':'bind','Destination':'/socket','Source':'/var/run/docker.sock'})
        else:
            f['config']['User'] = '0:0'
        assert not container_runtime(replace(r, facts_json=encode(f)), a, 'web', image)


def artifact_fixture(index=True):
    config = encode({'os':'linux','architecture':'amd64','config':{'User':'101:101'}}).encode()
    cd = blob_digest(config)
    manifest = encode({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
                      'config':{'digest':cd,'size':len(config),'mediaType':'application/vnd.oci.image.config.v1+json'},'layers':[]}).encode()
    md = blob_digest(manifest)
    idx = encode({'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':[
        {'digest':md,'size':len(manifest),'mediaType':'application/vnd.oci.image.manifest.v1+json',
         'platform':{'os':'linux','architecture':'amd64'}}]}).encode()
    blobs = {cd: config, md: manifest, blob_digest(idx): idx}
    reference = 'ghcr.io/fixtures/router@' + (blob_digest(idx) if index else md)
    source = SourceRequest('fixtures/router', 'a'*40, 'b'*40, reference, 'https://router.example.test')
    class Verifier:
        def verify(self, request):
            return SourceVerification(request, (), 'sha256:'+'e'*64)
    return reference, blobs, source, Verifier(), cd, md


def verify(ref, blobs, source, verifier):
    return verify_artifact(ref, 'ghcr.io/fixtures/router', blobs, source, verifier,
                           expected_source='fixtures/router', expected_origin='https://router.example.test')


@pytest.mark.parametrize('index',[True,False])
def test_index_manifest_config_mapping(index):
    ref, blobs, source, verifier, cd, md = artifact_fixture(index)
    e = verify(ref, blobs, source, verifier)
    assert e.image_id == cd and e.manifest_digest == md
    assert e.reference == ref and cd != md
    inspect = {'Id':cd,'RepoDigests':[ref],'Os':'linux','Architecture':'amd64','Config':{'User':'101:101'}}
    assert bind_engine_image(e, inspect) == e
    inspect['Id'] = md
    with pytest.raises(DockerError):
        bind_engine_image(e, inspect)


@pytest.mark.parametrize('case',['bytes','repository','revision','origin','source','verifier','ci','proof-subject','config-platform','platform','size','duplicate-platform'])
def test_artifact_rejection(case):
    ref, blobs, source, verifier, cd, md = artifact_fixture()
    if case == 'bytes': blobs[md] += b' '
    elif case == 'repository': ref = ref.replace('fixtures/router','foreign/router')
    elif case == 'revision': source = replace(source,producer_revision='main')
    elif case == 'origin': source = replace(source,build_origin=None)
    elif case == 'source': source = replace(source,repository='foreign/router')
    elif case == 'verifier': verifier = None
    elif case in ('ci','proof-subject'):
        class Bad:
            def verify(self, request):
                return SourceVerification(replace(request,manager_revision='c'*40) if case == 'proof-subject' else request,
                                          ('exact-sha-ci-unverified',) if case == 'ci' else (), 'sha256:'+'e'*64)
        verifier = Bad()
    else:
        doc = json.loads(blobs[ref.split('@')[1]])
        if case == 'platform': doc['manifests'][0]['platform']['architecture'] = 'arm64'
        elif case == 'size': doc['manifests'][0]['size'] += 1
        elif case == 'duplicate-platform': doc['manifests'].append(copy.deepcopy(doc['manifests'][0]))
        else:
            config = json.loads(blobs[cd]); config['architecture'] = 'arm64'
            raw = encode(config).encode(); newcd = blob_digest(raw); blobs[newcd] = raw
            m = json.loads(blobs[md]); m['config'].update(digest=newcd,size=len(raw))
            raw = encode(m).encode(); newmd = blob_digest(raw); blobs[newmd] = raw
            doc['manifests'][0].update(digest=newmd,size=len(raw))
        raw = encode(doc).encode(); digest = blob_digest(raw); blobs[digest] = raw
        ref = ref.split('@')[0]+'@'+digest; source = replace(source,reference=ref)
    with pytest.raises(DockerError): verify(ref,blobs,source,verifier)


def proxy():
    c = ProxyContract('172.30.0.0/24','172.30.0.1','d'*64)
    r = replace(resource('network','portfolio-proxy',{LABEL+'.managed':'true',LABEL+'.role':'proxy'},
                   internal=False,ipam={'Driver':'default','Options':{},'Config':[{'Subnet':c.subnet,'Gateway':c.gateway}]},
                   attachable=False,ingress=False,enableIPv6=False),identifier=c.network_id)
    return c, r


@pytest.mark.parametrize('key,value',[('driver','overlay'),('internal',True),('options',{'foreign':'true'}),
 ('ipam',{}),('labels',{}),('attachable',True),('ingress',True),('enableIPv6',True)])
def test_proxy_contract_fail_closed(key,value):
    c,r = proxy(); f=r.facts; f[key]=value
    with pytest.raises(DockerError): validate_proxy(DockerObservation((replace(r,facts_json=encode(f)),),(),True),c)


def test_proxy_identity_and_completeness():
    c,r=proxy(); o=DockerObservation((r,),(),True)
    validate_proxy(o,c)
    for changed in (replace(o,complete=False),replace(o,resources=(replace(r,identifier='e'*64),))):
        with pytest.raises(DockerError): validate_proxy(changed,c)


@pytest.mark.parametrize('kind',['network','volume'])
def test_foreign_project_resource_blocks_without_deletion(router,kind):
    a,r,image=state(router)
    c,net=proxy()
    foreign=resource(kind,'unreviewed',{ 'com.docker.compose.project':a.project,
                                      f'com.docker.compose.{kind}':'unknown'})
    o=DockerObservation((net,foreign), (image,),True)
    assert 'project-foreign-resource' in preflight_resources(router,a,o)
    # Truly unrelated objects are preserved and never become mutations.
    f=foreign.facts; f['labels']={}
    o=replace(o,resources=(net,replace(foreign,facts_json=encode(f))))
    assert not preflight_resources(router,a,o)


class GatewayPort:
    def __init__(self,routes): self.routes=routes; self.calls=[]; self.lkg=None; self.fail=None
    def read(self): return self.routes
    def validate(self,text):
        self.calls.append('validate')
        if self.fail=='validate': raise DockerError('caddy-invalid')
    def preserve_lkg(self,text): self.lkg=text; self.calls.append('lkg')
    def compare_and_reload(self,old,new):
        self.calls.append('reload')
        if self.fail=='reload': raise DockerError('caddy-unknown',True)
        self.routes=self.target


def test_gateway_retains_routes_and_image_only_no_reload(router):
    from scripts.gateway import desired_routes, GatewayRoute
    d=DesiredState(REVISION,(router,)); doc=observation(d)
    target=desired_routes(d)
    port=GatewayPort(target); g=ProductionGateway(port, lease_verifier=lambda lease: None)
    assert not g.activate(d,load_observed_state(doc),router.id,ready=True,lease=LockLease())
    assert not port.calls
    doc['applications'][0]['route'].update(existence='absent',configuration=None)
    other=GatewayRoute('other','web','other.example.test',8080,'other-web')
    other_app=copy.deepcopy(observation(d)['applications'][0]); other_app.update(id='other')
    other_app['owner']['application']='other'; other_app['route']['owner']['application']='other'
    other_app['route']['configuration']={'hostname':other.hostname,'component':'web','port':8080}
    # Disabled unrelated desired still must retain its active route.
    spec=router.spec; spec['enabled']=False; spec['ingress']=other_app['route']['configuration']
    unrelated=replace(router,id='other',spec_json=encode(spec))
    # Stage03 blocks unknown resources; provide the unrelated app's own runtime.
    od=observation(DesiredState(REVISION,(unrelated,)))['applications'][0]
    od['route']=other_app['route']; doc['applications'].append(od)
    d=replace(d,applications=(router,unrelated))
    port=GatewayPort((other,)); port.target=(*target,other)
    # Disabled active apps are intentionally retained by Stage04 but Stage03 may
    # block deactivation: exercise unrelated retention via an enabled stable app.
    spec['enabled']=True; unrelated=replace(unrelated,spec_json=encode(spec)); d=replace(d,applications=(router,unrelated))
    assert ProductionGateway(port, lease_verifier=lambda lease: None).activate(d,load_observed_state(doc),router.id,ready=True,lease=LockLease())
    assert port.calls==['validate','lkg','reload'] and port.lkg==content((other,))
    assert other in port.routes and 'admin unix//run/portfolio-caddy-admin/admin.sock|0600' in content(port.routes)


@pytest.mark.parametrize('failure',['validate','reload'])
def test_gateway_failure_keeps_lkg(router,failure):
    from scripts.gateway import desired_routes, make_route
    d=DesiredState(REVISION,(router,)); doc=observation(d)
    doc['applications'][0]['route']['configuration']['hostname']='old.example.test'
    old=(make_route(router.id,doc['applications'][0]['route']['configuration']),)
    port=GatewayPort(old); port.target=desired_routes(d); port.fail=failure
    with pytest.raises(DockerError): ProductionGateway(port, lease_verifier=lambda lease: None).activate(d,load_observed_state(doc),router.id,ready=True,lease=LockLease())
    assert port.routes==old
    if failure=='validate': assert port.calls==['validate']
    else: assert port.lkg==content(old)


def test_gateway_readiness_and_lock_gate(router):
    d=DesiredState(REVISION,(router,)); port=GatewayPort(())
    for ready,lease in ((False,LockLease()),(True,None),(True,LockLease(False))):
        with pytest.raises(DockerError): ProductionGateway(port, lease_verifier=lambda lease: None).activate(d,load_observed_state(empty()),router.id,ready=ready,lease=lease)
    assert not port.calls


def paths():
    return RuntimePaths('/srv/portfolio/state','/srv/portfolio/locks','/srv/portfolio/journal',
                        '/srv/portfolio/caddy-config','/srv/portfolio/certificates','/srv/portfolio/caddy-data',104857600)


def contract():
    c,_=proxy()
    return {'apiVersion':'portfolio.production-runtime/v1','paths':asdict(paths()),'proxy':asdict(c),
            'caddy':{'image':'caddy@sha256:'+'f'*64,'version':'2.10.2'}}


def test_caddy_runtime_and_operator_contract():
    runtime,_=validate_contract(contract()); model=runtime.render().model
    service=model['services']['caddy']
    assert service['ports']==['80:80/tcp','443:443/tcp']
    assert service['networks']==['proxy'] and service['read_only'] is True
    assert all('docker.sock' not in v['source'] for v in service['volumes'])
    assert service['volumes'][0]['read_only'] is True
    assert {v['target'] for v in service['volumes']}=={'/etc/caddy','/data','/config','/run/portfolio-caddy-admin'}
    assert '2019' not in encode(model)


@pytest.mark.parametrize('change',['tmp','relative','traversal','same','nested','budget','version','tag','extra'])
def test_operator_contract_rejects_unsafe(change):
    doc=contract()
    if change=='tmp': doc['paths']['lock']='/tmp/e2e/locks'
    elif change=='relative': doc['paths']['journal']='journal'
    elif change=='traversal': doc['paths']['state']='/srv/../state'
    elif change=='same': doc['paths']['certificate_data']=doc['paths']['journal']
    elif change=='nested': doc['paths']['journal']=doc['paths']['state']+'/journal'
    elif change=='budget': doc['paths']['certificate_budget_bytes']=True
    elif change=='version': doc['caddy']['version']='latest'
    elif change=='tag': doc['caddy']['image']='caddy:latest'
    else: doc['caddy']['ports']=['2019:2019']
    with pytest.raises(DockerError): validate_contract(doc)[0].render()


@pytest.mark.parametrize('command',['preflight','plan'])
def test_offline_cli_never_networks_or_mutates(router,tmp_path,monkeypatch,capsys,command):
    def forbidden(*a,**k): pytest.fail('offline preview contacted network/process')
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    p=tmp_path/'contract.json'; p.write_text(encode(contract()))
    args=[command,'--contract',str(p)]
    if command=='plan':
        observed=tmp_path/'observed.json'; observed.write_text(encode(empty()))
        args+=['--root',str(Path(__file__).resolve().parents[1]),'--observed',str(observed),'--git-revision',REVISION]
    assert main(args)==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='blocked' and result['authorization'] is False and result['executed'] is False
    assert 'stage-09-4-operation-authorization-unavailable' in result['blockers']


def test_production_adapter_no_write_capability(router,monkeypatch):
    adapter=ProductionDockerAdapter(EngineContract('unix:///var/run/docker.sock','daemon','a'*32,True),lambda:'a'*32)
    def forbidden(*a,**k): pytest.fail('write path contacted engine')
    monkeypatch.setattr(adapter,'identity',forbidden)
    for call in (lambda:adapter.authorize(render_compose(router)),lambda:adapter.up(render_compose(router),'web')):
        with pytest.raises(DockerError,match='stage-09-4'): call()


@pytest.mark.parametrize('case',['host','daemon','permission','platform','read-approval','remote-endpoint'])
def test_engine_identity_fail_closed(monkeypatch,case):
    from scripts.production_integration import docker
    monkeypatch.setattr(docker,'verify_socket',lambda endpoint: None)
    info={'ID':'daemon','OSType':'linux','Architecture':'x86_64'}
    if case=='daemon': info['ID']='foreign'
    if case=='platform': info['Architecture']='aarch64'
    def run(*args,**kwargs):
        if case=='permission': raise DockerError('docker-command-failed')
        return encode(info).encode()
    monkeypatch.setattr(docker,'run_bounded',run)
    c=EngineContract('unix:///var/run/docker.sock','daemon','a'*32,case!='read-approval')
    adapter=ProductionDockerAdapter(c,lambda:'b'*32 if case=='host' else 'a'*32); adapter.binary='/usr/bin/docker'
    if case=='remote-endpoint':
        monkeypatch.undo()
        c=replace(c,endpoint='ssh://server'); adapter=ProductionDockerAdapter(c,lambda:'a'*32)
    with pytest.raises(DockerError): adapter.identity()


def test_engine_inventory_reuses_bounded_read_commands(monkeypatch):
    from scripts.production_integration import docker
    calls=[]
    monkeypatch.setattr(docker,'run_bounded',lambda argv,**kw:calls.append(argv) or b'')
    adapter=ProductionDockerAdapter(EngineContract('unix:///var/run/docker.sock','daemon','a'*32,True),lambda:'a'*32)
    adapter.binary='/usr/bin/docker'; monkeypatch.setattr(adapter,'identity',lambda:('a'*32,'daemon'))
    assert adapter.inventory('container')==()
    assert calls[0][1:]==['--host','unix:///run/docker.sock','container','ls','--all','--quiet','--no-trunc']
    with pytest.raises(DockerError): adapter._run(['compose','up'])
    assert len(calls)==1


def reader_fixture(router,monkeypatch):
    from scripts.production_integration import observations as module
    a,r,image=state(router); pc,net=proxy()
    private=a.model['networks']['private']
    private_r=resource('network',private['name'],{**private['labels'],'com.docker.compose.project':a.project,
                                               'com.docker.compose.network':'private'})
    docker=DockerObservation((r,net,private_r),(image,),True)
    monkeypatch.setattr(module,'discover',lambda *args,**kwargs:docker)
    d=DesiredState(REVISION,(router,)); doc=observation(d)
    from scripts.gateway import desired_routes
    class Port:
        def __init__(self,identity,payload): self.identity=identity; self.payload=payload; self.change=lambda r:r
        def collect(self,round_id):
            return self.change(SourceResult(self.identity,round_id,0,True,True,self.payload))
    class Engine:
        def identity(self): return 'a'*32,'daemon'
    ports=[Port('runtime-v1',doc),Port('gateway-v1',desired_routes(d)),Port('readiness-v1',{r.identifier:'healthy'})]
    from scripts.production_integration.trust import SourceTrust, SourceBinding, SourceAttestation
    class Attestor:
        def verify(self, binding, port, result):
            return SourceAttestation(binding, result)  # test-only authenticated channel
    trust = SourceTrust(tuple(SourceBinding(name, 'a'*32, 'daemon', port.identity)
                              for name, port in zip(('runtime', 'gateway', 'readiness'), ports)), Attestor())
    reader=ProductionStateReader(lambda:d,Engine(),*ports,lambda ref,raw:image,pc,clock=lambda:0,source_trust=trust)
    return reader,ports,docker


def test_complete_observations_use_composite_and_stage03(router,monkeypatch):
    reader,ports,docker=reader_fixture(router,monkeypatch)
    provider=reader.provider(); d,o,actual=provider.refresh()
    assert actual==docker and build_reconciliation_plan(d,o).document['status']=='no-op'
    assert {s.source for s in provider.sample.sources}=={'docker','runtime','gateway','readiness'}


@pytest.mark.parametrize('case',['partial','unverified','stale','round','error','gateway','readiness','runtime','docker','racing','daemon'])
def test_incomplete_observation_never_promoted(router,monkeypatch,case):
    from scripts.production_integration import observations as module
    reader,ports,docker=reader_fixture(router,monkeypatch)
    if case in ('partial','unverified','stale','round','error'):
        changes={'partial':{'complete':False},'unverified':{'verified':False},'stale':{'captured_at':-1},
                 'round':{'round_id':'old'},'error':{'error':'permission-denied'}}
        ports[0].change=lambda r:replace(r,**changes[case])
    elif case=='gateway': ports[1].payload=()
    elif case=='readiness': ports[2].payload={}
    elif case=='runtime': ports[0].payload['applications'][0]['components'][0]['runtime']['runtimeSecurity']['user']='102:102'
    elif case=='docker': monkeypatch.setattr(module,'discover',lambda *a,**kw:replace(docker,complete=False))
    elif case=='racing':
        calls=[]
        def discover(*a,**kw): calls.append(1); return docker if len(calls)==1 else replace(docker,resources=docker.resources[1:])
        monkeypatch.setattr(module,'discover',discover)
    else:
        calls=[]
        def identity(): calls.append(1); return ('host','daemon' if len(calls)==1 else 'foreign')
        reader.docker.identity=identity
    provider=reader.provider()
    with pytest.raises(DockerError): provider.refresh()
    assert provider.sample is None


def rehearsal(world,tmp_path,probes=None,gateway_error=False,adapter=None):
    class Gateway:
        def __init__(self): self.calls=[]
        def activate(self,*args,**kw):
            assert kw['lease'].active and kw['ready'] is True
            self.calls.append('activate')
            if gateway_error: raise DockerError('caddy-failed',True)
    gateway=Gateway(); clock=Clock()
    lifecycle=RehearsalLifecycle(tmp_path/'j',probes or Probes(),gateway=gateway,policy=POLICY,
                                  clock=clock.now,sleep=clock.sleep)
    adapter=adapter or world.adapter()
    _,observed,_=world.provider.refresh(); plan=build_reconciliation_plan(world.desired,observed)
    results=execute(world.desired,observed,plan,world.app.id,adapter,world.provider,
                    lock_directory=tmp_path/'l',lifecycle=lifecycle)
    return results,adapter,gateway,lifecycle


@pytest.fixture
def rehearsal_app(root):
    put(root,'project-router')
    return build_desired_state(root,REVISION).applications[0]


def test_same_executor_journal_lkg_and_noop(rehearsal_app,tmp_path):
    world=World(rehearsal_app)
    results,adapter,gateway,lifecycle=rehearsal(world,tmp_path)
    assert results[0].release_success and len(adapter.calls)==1 and gateway.calls==['activate']
    with locked_journal(tmp_path/'j',lock_directory=tmp_path/'l') as journal:
        assert journal.last_good(rehearsal_app.id,'web')['run']==lifecycle.run_id
    results,adapter,gateway,_=rehearsal(world,tmp_path)
    assert results[0].release_success and not adapter.calls


@pytest.mark.parametrize('case',['readiness','gateway','interruption'])
def test_rehearsal_failure_never_promotes_lkg(rehearsal_app,tmp_path,case):
    from scripts.readiness import ProbeResult
    world=World(rehearsal_app)
    results,adapter,gateway,_=rehearsal(world,tmp_path,
        probes=Probes([ProbeResult(True,503)]) if case=='readiness' else None,
        gateway_error=case=='gateway',adapter=world.adapter(DockerError('timeout',True)) if case=='interruption' else None)
    assert not results[0].release_success and results[0].status=='recovery-required'
    if case!='gateway': assert not gateway.calls
    assert len(adapter.calls)==1
    with locked_journal(tmp_path/'j',lock_directory=tmp_path/'l') as j:
        assert j.last_good(rehearsal_app.id,'web') is None and j.incomplete()
