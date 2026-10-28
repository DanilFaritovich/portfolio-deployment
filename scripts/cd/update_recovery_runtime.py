"""Sealed root-only read adapters for verification of an applied update."""
import base64
from dataclasses import asdict
from pathlib import Path
import time

from scripts.cd.update_recovery import UpdateRecovery
from scripts.cd.release import ImageRelease, ImageReleaseVerifier, ProjectedContent, source_file
from scripts.cd.signatures import authenticate, REQUEST_NAMESPACE
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import authenticate_signed, canonical, closed_json


def authenticated_release(raw, journal, run, *, standing, github, registry, crypto):
    """Revalidate the retained automation request; old authority is never reused."""
    from scripts.cd.policy import pin
    signed = standing.current().document
    role = signed['automation']
    request = closed_json(raw, 32768)
    require(type(request) is dict and set(request) == {'op', 'request', 'context'}
            and request['op'] == 'application', 'update-recovery-request-invalid')
    doc = request['request']
    require(type(doc) is dict and set(doc) == {'op', 'release', 'candidate', 'envelope'}
            and doc['op'] == 'execute', 'update-recovery-request-invalid')
    c = OperationCandidate(**doc['candidate'])
    c.validate()
    row = journal.db.execute('SELECT * FROM authorizations WHERE nonce=?', (run,)).fetchone()
    require(row is not None and c.scope == 'update-project-router'
            and c.identity == row['candidate'] and canonical(asdict(c)).decode() == row['payload']
            and row['state'] == 'recovery-required' and row['operator'] == role['principal']
            and journal.production_bootstrap(c.host, c.daemon),
            'update-recovery-request-unbound')
    envelope = base64.b64decode(doc['envelope'], validate=True)
    claims = authenticate_signed(envelope, {role['principal']: role['key']})
    require(claims.nonce == run and claims.candidate == c.identity
            and claims.not_before == row['not_before'] and claims.expires == row['expires'],
            'update-recovery-original-approval-unbound')
    context, _ = authenticate(base64.b64decode(request['context'], validate=True),
        keys={role['principal']: role['key']}, namespace=REQUEST_NAMESPACE)
    release = ImageRelease(**doc['release']).validate()
    require(set(context) == {'approval_sha256', 'candidate', 'repository', 'source_sha',
                            'run_id', 'run_attempt', 'not_before', 'expires'}
            and context['approval_sha256'] == pin(envelope)
            and context['candidate'] == c.identity
            and context['repository'] == 'DanilFaritovich/project-router'
            and context['source_sha'] == c.producer_sha == release.source_sha
            and c.image == release.image and type(context['run_id']) is int
            and context['run_id'] > 0 and type(context['run_attempt']) is int
            and context['run_attempt'] > 0 and type(context['not_before']) is int
            and type(context['expires']) is int
            and 0 < context['expires'] - context['not_before'] <= 300,
            'update-recovery-original-context-unbound')
    workflow = signed['deployment_workflows'][context['repository']]
    require(pin(source_file(github, context['repository'], workflow['path'], context['source_sha']))
            == workflow['sha256'], 'update-recovery-workflow-untrusted')
    run_doc = github.get(context['repository'], '/actions/runs/' + str(context['run_id']))
    require(run_doc['id'] == context['run_id'] and run_doc['run_attempt'] == context['run_attempt']
            and run_doc['path'] == workflow['path'] and run_doc['head_sha'] == context['source_sha']
            and run_doc['event'] == 'workflow_run' and run_doc['head_branch'] == 'main'
            and run_doc['status'] == 'completed' and run_doc['conclusion'] == 'failure'
            and run_doc['repository']['full_name'] == context['repository']
            and run_doc['head_repository']['full_name'] == context['repository'],
            'update-recovery-original-run-untrusted')
    ImageReleaseVerifier(standing, github, registry, crypto).verify(release)
    return release, pin(raw)


def installed_recovery(installation, standing, fixed):
    from scripts.cd.administrative import CAPSULE, private
    from scripts.cd.application import CHALLENGE, CURRENT, STATE, release_ports
    from scripts.cd.bootstrap_containment import digest
    from scripts.cd.host import StandingReview
    from scripts.cd.rollout_recovery import (await_public_witness, build_recovery_state_reader,
        observe_updated_release, recovery_public_acceptance)
    from scripts.gateway import desired_routes
    from scripts.production_integration.artifacts import bind_engine_image, verify_oci_bytes
    from scripts.production_integration.caddy_release import CaddyReleaseCollector
    from scripts.production_integration.fixed_bindings import FixedPolicy, GH, ROOT, TOKEN
    from scripts.production_integration.gateway_write import durable_replace
    from scripts.production_integration.installation import protected
    from scripts.production_integration.locking import production_host_lock, require_production_lease
    from scripts.production_integration.operator_approval import TRUST, trust_keys
    from scripts.production_integration.public_postconditions import ORIGIN, PATH
    from scripts.production_integration.release import MANAGER
    from scripts.production_integration.release_crypto import AttestationVerifier
    from scripts.production_integration.release_source import WorkflowCheck
    from scripts.production_integration.release_transport import GHCRRead, HTTPSRead, IMAGE_REPOSITORY
    from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths
    from scripts.readiness import ReadinessPolicy

    require(type(fixed) is FixedPolicy, 'update-recovery-fixed-policy-required')
    installation.verify()
    pins = dict(installation.files)
    require(pins.get(GH) == fixed.gh_sha256 and pins.get(ROOT) == fixed.sigstore_root_sha256
            and pins.get(fixed.public.ca_file) == fixed.public.ca_sha256
            and str(TRUST) in pins, 'update-recovery-external-trust-unbound')
    review = StandingReview(installation, standing)
    github, registry, crypto, tls = release_ports(fixed)
    sources = None
    source_identity = None

    def release_reader(journal, run):
        path = STATE/'requests'/(run+'.json')
        raw = private(path, 32768)
        return authenticated_release(raw, journal, run, standing=standing,
                                     github=github, registry=registry, crypto=crypto)

    def observe(candidate, app, release, lease):
        nonlocal sources, source_identity
        require_production_lease(lease)
        identity = (candidate.identity, release)
        if sources is None:
            blobs = verify_oci_bytes(release.image, IMAGE_REPOSITORY, registry.collect(release.image))
            caddy = CaddyRuntime(fixed.caddy_image, fixed.caddy_version, RuntimePaths(
                *(f'/srv/portfolio/{p}' for p in ('state','locks','journal','caddy-config',
                                                 'certificates','caddy-data')),
                fixed.certificate_budget_bytes))
            token = private(TOKEN, 4096).decode('ascii').strip()
            cr = GHCRRead(HTTPSRead(context=tls), repository='ghcr.io/danilfaritovich/portfolio-caddy',
                         registry_auth=(fixed.ghcr_user, token))
            ce = CaddyReleaseCollector(github=github, registry=cr,
                crypto=AttestationVerifier(GH, fixed.gh_sha256, ROOT, fixed.sigstore_root_sha256, repository=MANAGER),
                checks=(WorkflowCheck(MANAGER,'Offline checks',15368,'.github/workflows/offline.yml'),),
                publication_check=WorkflowCheck(MANAGER,'Publish trusted Caddy / Publish',15368,
                                                '.github/workflows/caddy-image.yml')).collect(caddy,fixed.caddy_source_sha)
            public = recovery_public_acceptance(candidate,app,standing=standing,
                installation=installation,installed_public=fixed.public,update_release=release)
            provider, probes = build_recovery_state_reader(installation=installation,
                standing_review=review,candidate=candidate,app=app,caddy=caddy,caddy_image=ce,
                proxy_subnet=fixed.proxy_subnet,proxy_gateway=fixed.proxy_gateway,
                private_subnet=fixed.private_subnet,
                resolve_image=lambda ref,inspected:bind_engine_image(blobs,inspected),
                public_postconditions=public,update_release=release)
            sources=(public,provider,probes)
            source_identity=identity
        require(source_identity == identity, 'update-recovery-source-changed')
        public,provider,probes=sources
        from scripts.production_integration.candidate import hashed
        from scripts.reconciliation import DesiredState
        routes=desired_routes(DesiredState(candidate.manager_sha,(app,)))
        subject={'origin':ORIGIN,'path':PATH,'identity':[candidate.host,candidate.daemon],
                 'routes':[asdict(r) for r in routes],'policy':hashed(asdict(public.policy))}
        def publish():
            installation.verify()
            standing.current()
            protected(fixed.public.ca_file)
            with Path(fixed.public.ca_file).open('rb') as stream:
                ca=stream.read(1024*1024+1)
            require(0 < len(ca) <= 1024*1024, 'update-recovery-ca-invalid')
            require(digest(ca)==fixed.public.ca_sha256,'update-recovery-ca-changed')
            durable_replace(STATE,CHALLENGE.name,canonical({'policy':asdict(public.policy),
                'routes':[asdict(r) for r in routes],'ca':base64.b64encode(ca).decode(),
                'created':int(time.time()),'policy_id':standing.identity}))
        return observe_updated_release(candidate,app,provider,probes,ReadinessPolicy(),
            public_postconditions=public,before_public=lambda:await_public_witness(publish,subject))

    def current():
        return private(CURRENT,8192) if CURRENT.exists() else None

    return UpdateRecovery(installation=installation,policy=standing,lock=production_host_lock,
        release_reader=release_reader,
        application_reader=lambda r:ProjectedContent(installation,standing,r)()[0].applications[0],
        observe=observe,inventories=lambda:{'inventory_sha256':digest(private(Path('/opt/portfolio-deployment/installation.json'),2*1024*1024)),
            'capsule_sha256':digest(private(CAPSULE/'capsule.json',2*1024*1024))},
        current_reader=current,current_writer=lambda raw:durable_replace(STATE,CURRENT.name,raw),
        keys=trust_keys(private(TRUST,8192)))


def main():
    import os
    import sys
    from scripts.cd.application import installed_policy
    from scripts.cd.bridge import installation
    require(os.geteuid()==0, 'update-recovery-root-required')
    request=closed_json(sys.stdin.buffer.read(131073),131072)
    require(request=={'op':'prepare'} or (type(request) is dict and set(request)=={'op','envelope'}
            and request['op']=='execute' and type(request['envelope']) is str),
            'update-recovery-operation-invalid')
    import cd_bindings
    installed=installation()
    operation=installed_recovery(installed,installed_policy(installed),cd_bindings.POLICY)
    result=operation.prepare() if request['op']=='prepare' else operation.execute(
        base64.b64decode(request['envelope'],validate=True))
    print(canonical(result).decode())
    return 0
