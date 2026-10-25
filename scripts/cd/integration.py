"""Opt-in real local Docker/CD lifecycle acceptance using existing E2E adapters.

No production manifests/keys/endpoints. OCI trust is the separate fixture lock;
source/Sigstore/bridge rejection tests are offline. This proves real rollout and
public-gate rollback mechanics, not production delivery or public DNS/ACME.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import secrets
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid

import yaml

from scripts.compose import LABEL, render_compose
from scripts.deployment import execute
from scripts.deployment_journal import locked_journal
from scripts.deployment_lifecycle import DeploymentLifecycle
from scripts.docker_adapter import DockerError, LocalDockerAdapter, LocalScope
from scripts.docker_discovery import discover
from scripts.e2e.images import Images
from scripts.e2e.safety import GuestDocker
from scripts.e2e.observations import Reader
from scripts.e2e.transports import Probes, request
from scripts.local_docker_smoke import verify_cleanup
from scripts.planning_inputs import build_desired_state
from scripts.production_integration.execution import ProductionLifecycle
from scripts.production_integration.public_postconditions import PublicPolicy, PublicPostconditions, tls_context
from scripts.cd.signatures import sign, authenticate
from scripts.production_integration.public_witness import NAMESPACE as WITNESS_NAMESPACE
from scripts.production_integration.operator_approval import canonical
from scripts.gateway import GatewayRoute
from scripts.readiness import ReadinessPolicy, ProbeResult
from scripts.reconciliation import build_reconciliation_plan


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


class NoPublicRoutes:
    def observations(self, desired, endpoints):
        require(all(a.spec['ingress'] is None for a in desired.applications), 'test-only-no-public-routes')
        return {}, True

    def activate(self, *args, **kwargs):
        raise RuntimeError('unexpected-gateway-mutation')


class TestLifecycle(ProductionLifecycle):
    def _verify(self, app, name):
        try:
            return super()._verify(app,name)
        except Exception as error:
            (Path(self.directory).parent/'probe-failure.txt').write_text(type(error).__name__+': '+str(error))
            raise

    @contextmanager
    def open(self, lease, adapter, provider):
        # Existing Stage06 disposable gate, never a production grant substitute.
        with DeploymentLifecycle.open(self, lease, adapter, provider):
            yield self


class TestDocker(LocalDockerAdapter):
    budget = GuestDocker.budget

    def __init__(self, scope):
        self.deadline = None
        super().__init__(scope)

    def _run(self, args, *, timeout=20, cwd=None):
        if self.deadline is not None:
            timeout = min(timeout, self.deadline-time.monotonic())
            require(timeout>0,'test-probe-budget-exhausted')
        return super()._run(args,timeout=timeout,cwd=cwd)


class LocalProbes(Probes):
    def probe(self,*args,**kwargs):
        try:
            return super().probe(*args,**kwargs)
        except DockerError as error:
            if error.code != 'docker-command-failed':
                raise
            # The bounded HTTP helper returns a nonzero status while the freshly
            # started fixture is not listening. Unknown health is an observation,
            # never a healthy source or reason to skip the readiness deadline.
            return ProbeResult(None)


class LoopbackHTTPS:
    """Disposable TLS proxy to a freshly inspected numeric container endpoint.

    Explicit test transport maps the public policy to loopback. No production
    DNS, CA, key, listener or endpoint is read or modified.
    """
    def __init__(self, directory, backend):
        self.directory, self.backend, self.fail, self.wrong_name = directory, backend, False, False
        key, cert = directory/'tls.key', directory/'tls.crt'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(key),'-out',str(cert),'-subj','/CN=danil-miftakhov.dev',
            '-addext','subjectAltName=DNS:danil-miftakhov.dev'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.cert=cert
        proxy=self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                data=b''
                while b'\r\n\r\n' not in data:
                    chunk=self.request.recv(4096)
                    if not chunk or len(data)>16384:return
                    data+=chunk
                status,body=proxy.backend()
                if proxy.fail and body==b'v1':status=503
                raw=(f'HTTP/1.1 {status} OK\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()+body
                self.request.sendall(raw)
        self.server=socketserver.TCPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert),str(key))
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.observer_key=directory/'observer.key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.observer_key)],check=True)
        self.observer_public=' '.join(self.observer_key.with_suffix('.key.pub').read_text().split()[:2])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def resolve(self,resolver,kind,deadline):
        return ('8.8.8.8',) if kind==1 else ()

    def https(self,address,ca,deadline):
        with socket.create_connection(self.server.server_address,timeout=min(2,deadline-time.monotonic())) as raw:
            with tls_context(ca).wrap_socket(raw,server_hostname='wrong.example.test' if self.wrong_name else 'danil-miftakhov.dev') as channel:
                channel.sendall(b'GET /en/ HTTP/1.1\r\nHost: danil-miftakhov.dev\r\nConnection: close\r\n\r\n')
                result=b''
                while True:
                    part=channel.recv(65536)
                    if not part:break
                    result+=part
                    require(len(result)<=300000,'test-https-output-limit')
                return result

    def accept(self,body):
        public=PublicPolicy(('a'*32,'test-daemon'),('8.8.8.8',),(),'8.8.8.8',str(self.cert),
            hashlib.sha256(self.cert.read_bytes()).hexdigest(),hashlib.sha256(body).hexdigest())
        routes=(GatewayRoute('project-router','web','danil-miftakhov.dev',8080,'project-router-web',git_revision='a'*40),)
        proxy=self
        class IndependentWitness:
            def verify(self,subject,*,timeout):
                independent=PublicPostconditions(public,external=None,network=proxy).probe(routes,public.identity)
                require(independent==subject,'independent-https-subject-mismatch')
                now=int(time.time())
                receipt=sign({'subject':independent,'observed_at':now,'expires':now+30},
                    principal='fixture-observer',key=proxy.observer_key,namespace=WITNESS_NAMESPACE)
                authenticated,_=authenticate(receipt,keys={'fixture-observer':proxy.observer_public},namespace=WITNESS_NAMESPACE)
                require(authenticated['subject']==subject,'test-witness-signature-mismatch')
                return subject
        PublicPostconditions(public,external=IndependentWitness(),network=self).verify(routes,public.identity)


def registry(root, images):
    (root/'registry/applications').mkdir(parents=True)
    (root/'policy').mkdir()
    policies = []
    for name, role in (('e2e-cd','v1'),('e2e-sentinel','data1')):
        component = {'name':'web','image':images.records[role],'platform':'linux/amd64','type':'http',
                     'port':8080,'health':{'strategy':'http','path':'/health'}}
        storage = {'type':'stateless'}
        if name == 'e2e-sentinel':
            component['volumeMounts'] = [{'volume':'data','mountPath':'/data'}]
            storage = {'type':'persistent','volumes':[{'name':'data'}]}
        doc = {'apiVersion':'portfolio.deployment/v1','kind':'Application',
               'metadata':{'id':name,'sourceRepository':'fixtures/local-e2e'},
               'spec':{'enabled':True,'components':[component],'storage':storage}}
        (root/f'registry/applications/{name}.yaml').write_text(yaml.safe_dump(doc))
        policies.append({'id':name,'sourceRepository':'fixtures/local-e2e',
                         'imageRepositories':{'web':sorted({r.split('@')[0] for r in images.records.values()})},
                         'hostnames':[],'platforms':['linux/amd64']})
    (root/'policy/admission.yaml').write_text(yaml.safe_dump(
        {'apiVersion':'portfolio.deployment/v1','kind':'AdmissionPolicy','applications':policies}))


def run(endpoint, lockfile, output):
    require(not output.exists(), 'new-evidence-directory-required')
    output.mkdir(mode=0o700)
    session = secrets.token_hex(8)
    scope = LocalScope(endpoint, session, True)
    images = Images(lockfile)
    adapter = TestDocker(scope)
    adapter.prerequisites()
    initial = discover(adapter, ())
    require(initial.complete, 'initial-inventory-incomplete')
    baseline = {(r.kind,r.identifier) for r in initial.resources}
    for ref in images.records.values():
        # Explicitly authorized disposable fixture acquisition only. No production
        # repository, mutable tag, credential helper, build or implicit pull.
        adapter._run(['image','pull','--platform','linux/amd64',ref],timeout=120)
        images.evidence(ref,adapter.inspect('image',ref))
    root = output/'registry'
    registry(root,images)
    probes = LocalProbes(adapter)
    reader = Reader(root,'a'*40,adapter,images,NoPublicRoutes(),probes,local_scope=scope)
    provider = reader.provider()
    artifacts = tuple(render_compose(a,test_session=session) for a in build_desired_state(root,'a'*40).applications)
    checks = []
    def apply(app_id, *, public=None):
        desired,observed,_ = provider.refresh()
        lifecycle = TestLifecycle(SimpleNamespace(directory=output/'journal'),
            SimpleNamespace(claims=SimpleNamespace(nonce=uuid.uuid4().hex)),probes,gateway=NoPublicRoutes(),
            public_acceptance=public,side_effects_excluded=app_id=='e2e-cd',
            policy=ReadinessPolicy(deadline=12,interval=.5))
        return execute(desired,observed,build_reconciliation_plan(desired,observed),app_id,adapter,provider,
                       lock_directory=output/'locks',lifecycle=lifecycle)
    def change(role):
        path = root/'registry/applications/e2e-cd.yaml'
        doc = yaml.safe_load(path.read_text())
        doc['spec']['components'][0]['image'] = images.records[role]
        path.write_text(yaml.safe_dump(doc))
    def marker():
        provider.refresh()
        return request(reader.endpoints[('e2e-sentinel','web')],8080,'/data')[1]
    class Public:
        fail = False
        calls = []
        def verify(self, app):
            provider.refresh()
            status,body = request(reader.endpoints[(app.id,'web')],8080,'/health')
            self.calls.append(body.decode())
            require(status==200 and not (self.fail and body==b'v1'), 'external-acceptance-rejected')
            return True
    public = Public()
    https = LoopbackHTTPS(output,lambda:request(reader.endpoints[('e2e-cd','web')],8080,'/health'))
    class HTTPSAcceptance:
        fail=False
        wrong_name=False
        def verify(self,app):
            provider.refresh()
            target=app.spec['components'][0]['image']
            https.fail=self.fail
            https.wrong_name=self.wrong_name and target==images.records['v1']
            https.accept(b'v1' if target==images.records['v1'] else b'v2')
            return True
    public=HTTPSAcceptance()
    try:
        initial_result=apply('e2e-sentinel')
        require(bool(initial_result) and all(r.release_success for r in initial_result),
                'sentinel-not-ready:'+str([(r.status,r.reasons) for r in initial_result]))
        saved = marker()
        require(all(r.release_success for r in apply('e2e-cd',public=public)), 'initial-not-ready')
        inode = (output/'locks/host.lock').stat().st_ino
        checks.append('real-create-and-readiness')
        change('v2')
        require(all(r.release_success for r in apply('e2e-cd',public=public)), 'update-not-ready')
        checks.append('real-image-update')
        before = adapter.inspect('container',f'pd-smoke-{session}-e2e-cd-web-1')['Id']
        require(all(r.release_success for r in apply('e2e-cd',public=public)), 'no-op-failed')
        require(adapter.inspect('container',f'pd-smoke-{session}-e2e-cd-web-1')['Id']==before,'no-op-recreated-container')
        checks.append('real-no-op')
        change('bad')
        result = apply('e2e-cd',public=public)
        require(result and all(r.status=='rolled-back-verified' for r in result),'unhealthy-not-rolled-back')
        checks.append('real-unhealthy-rollback')
        # v2 remains LKG. Reject otherwise healthy v1 at the external gate.
        change('v1')
        public.fail = True
        result = apply('e2e-cd',public=public)
        require(result and all(r.status=='rolled-back-verified' for r in result),'external-not-rolled-back')
        checks.append('real-external-gate-rollback')
        public.fail=False
        public.wrong_name=True
        result=apply('e2e-cd',public=public)
        require(result and all(r.status=='rolled-back-verified' for r in result),'tls-failure-not-rolled-back')
        checks.append('real-tls-hostname-failure-rollback')
        require(marker()==saved,'persistent-marker-changed')
        require((output/'locks/host.lock').stat().st_ino==inode,'lock-inode-changed')
        with locked_journal(output/'journal',lock_directory=output/'locks') as journal:
            require(not journal.incomplete(),'unexpected-recovery-fence')
            require(journal.last_good('e2e-cd','web')['target']==images.records['v2'],'lkg-changed-on-failure')
        checks.append('real-journal-lkg-lock-and-persistence-preserved')
    finally:
        https.close()
        # Validate entire test ownership before removing only our containers and
        # empty networks. Keep every volume, image and evidence/journal directory.
        targets = verify_cleanup(discover(adapter,()),artifacts,session,baseline)
        for kind in ('container','network'):
            for resource in targets:
                if resource.kind!=kind:continue
                current=verify_cleanup(discover(adapter,()),artifacts,session,baseline)
                require(any(r.identifier==resource.identifier and r.kind==kind for r in current),'cleanup-target-changed')
                if kind=='container':adapter._run(['container','stop','--time','5',resource.identifier],timeout=15)
                adapter._run([kind,'rm',resource.identifier if kind=='container' else resource.name])
    report={'realDocker':True,'productionPerformed':False,'session':session,'checks':checks,
            'publicProbe':'real-loopback-TLS-proxy and independent SSHSIG; no public DNS/ACME','volumes':'retained',
            'sourceTrust':'separate offline tests; test fixture OCI lock only'}
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--image-lock',required=True,type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--authorize',required=True,choices=['disposable-cd-integration'])
    args=parser.parse_args()
    print(json.dumps(run(args.endpoint,args.image_lock,args.evidence)))


if __name__=='__main__':main()
