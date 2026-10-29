"""Installed production composition; rollout remains in ProductionExecutor."""
import base64
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import time

from scripts.cd.host import StandingReview
from scripts.cd.policy import AutomationVerifier, pin, read_policy
from scripts.cd.release import ImageRelease, ImageReleaseVerifier, ProjectedContent, require_forward_release
from scripts.production_integration.artifacts import bind_engine_image, verify_oci_bytes
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.candidate import hashed
from scripts.production_integration.fixed_bindings import (FixedPolicy, TOKEN, ROOT, GH, build_fixed)
from scripts.production_integration.gateway_write import durable_replace
from scripts.production_integration.installation import protected
from scripts.production_integration.local_sources import installed_engine
from scripts.production_integration.docker import EngineContract
from scripts.production_integration.locking import production_host_lock, require_production_lease
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import TRUST, canonical, closed_json, trust_keys
from scripts.production_integration.public_postconditions import PublicPostconditions, tls_context
from scripts.production_integration.public_witness import SignedPublicWitness
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GitHubRead, GHCRRead, HTTPSRead, IMAGE_REPOSITORY
from scripts.gateway import desired_routes

STATE = Path('/var/lib/portfolio-deployment-cd')
CHALLENGE = STATE / 'challenge.json'
CURRENT = STATE / 'current-release.json'


def installed_policy(installation):
    installation.verify()
    require(str(TRUST) in dict(installation.files), 'cd-operator-trust-unpinned')
    protected(TRUST)
    return read_policy(trust_keys(TRUST.read_bytes()))


def release_ports(fixed):
    protected(TOKEN)
    ca = Path(fixed.public.ca_file).read_bytes()
    require(pin(ca) == fixed.public.ca_sha256, 'cd-ca-pin-mismatch')
    context = tls_context(ca)
    github = GitHubRead(TOKEN.read_text().strip(), HTTPSRead(context=context))
    registry = GHCRRead(HTTPSRead(context=context))
    crypto = AttestationVerifier(GH, fixed.gh_sha256, ROOT, fixed.sigstore_root_sha256)
    return github, registry, crypto, context


class ActiveRoutes:
    """Observation is ACTIVE/runtime evidence. Public release acceptance is separate."""
    def __init__(self, policy):
        self.policy = policy

    def verify(self, routes, identity):
        self.policy.current()
        require(identity == (self.policy.document['host'], self.policy.document['daemon']) and
                len(routes) == 1 and routes[0].key == ('project-router', 'web', 'danil-miftakhov.dev', 8080, 'project-router-web'),
                'cd-active-route-contract-mismatch')
        return routes


class PublicAcceptance:
    def __init__(self, installation, standing, fixed, releases, desired):
        self.installation, self.standing, self.fixed = installation, standing, fixed
        self.releases = {r.image: r for r in releases}
        self.desired = desired

    def verify(self, app):
        self.standing.current()
        reference = app.spec['components'][0]['image']
        require(reference in self.releases, 'cd-public-release-unregistered')
        public = replace(self.fixed.public, body_sha256=self.releases[reference].html_sha256)
        routes = desired_routes(replace(self.desired, applications=(app,)))
        # The observer signs only AFTER probing independently. This challenge is
        # not success evidence and never grants authority to the observer key.
        payload = {'policy': asdict(public), 'routes': [asdict(r) for r in routes],
                   'ca': base64.b64encode(Path(public.ca_file).read_bytes()).decode(),
                   'created': int(time.time()), 'policy_id': self.standing.identity}
        durable_replace(STATE, CHALLENGE.name, canonical(payload))
        witness = SignedPublicWitness(self.installation, self.standing.document['observer']['key'])
        PublicPostconditions(public, external=witness).verify(routes, public.identity)
        return True


def compose(installation, standing, fixed, release):
    require(type(fixed) is FixedPolicy, 'cd-fixed-bindings-required')
    github, registry, crypto, context = release_ports(fixed)
    ImageReleaseVerifier(standing, github, registry, crypto).verify(release)
    if CURRENT.exists():
        protected(CURRENT)
        previous = ImageRelease(**json.loads(CURRENT.read_bytes())).validate()
    else:
        previous = ImageRelease(**standing.document['application']['initial_release']).validate()
    require_forward_release(previous,release,github)
    # Stored current-release is written only after journal promotion under flock.
    # The initial baseline is explicitly bound by the operator-signed policy.
    content = ProjectedContent(installation, standing, release)
    old_content = ProjectedContent(installation, standing, previous)
    desired, _, _ = content()
    old = old_content()[0].applications[0]
    catalog = tuple(dict.fromkeys((old, *desired.applications)))
    # Immutable OCI bytes are authenticated before the five-second mutable-host
    # observation window. Rebind EVERY fresh Engine inspection to these exact
    # digests; repeated remote registry reads cannot make immutable bytes fresher.
    immutable_images = {r.image: verify_oci_bytes(r.image, IMAGE_REPOSITORY,
        GHCRRead(HTTPSRead(context=context)).collect(r.image)) for r in (previous, release)}
    review = StandingReview(installation, standing)
    engine = installed_engine(installation, EngineContract('unix:///run/docker.sock',
        standing.document['daemon'], standing.document['host'], True))
    def resolve_image(reference, inspected):
        require(reference in immutable_images,'cd-image-outside-verified-catalog')
        return bind_engine_image(immutable_images[reference], inspected)
    review.engine, review.resolve_image, review.catalog = engine, resolve_image, catalog
    public = PublicAcceptance(installation, standing, fixed, (previous, release), desired)
    selected = replace(fixed, producer_sha=release.source_sha, image=release.image,
        signer_sha=release.source_sha, observer_key=standing.document['observer']['key'],
        slsa_builder=standing.document['application']['slsa_builder'],
        producer_checks=tuple(WorkflowCheck(**v) for v in standing.document['application']['checks']),
        publication_check=WorkflowCheck(**standing.document['application']['publication']))
    def committed(candidate, lease):
        if candidate.scope == 'update-project-router':
            require_production_lease(lease)
            durable_replace(STATE, CURRENT.name, canonical(asdict(release)))
    executor = build_fixed(installation, selected, review=review, content_reader=content, contracts=catalog,image_resolver=resolve_image,
        gateway_postconditions=ActiveRoutes(standing), composition_options={
            'facts_policy': review, 'content_verifier': content.verify_source_binding,
            'rollback_application': old, 'completion': committed,
            'lifecycle_options': {'side_effects_excluded': True, 'public_acceptance': public}})
    return executor, public


def handle(installation, request):
    require(type(request) is dict and set(request) <= {'op', 'release', 'candidate', 'envelope'} and
            request.get('op') in ('prepare-cache', 'prepare-update', 'execute'), 'cd-application-request-invalid')
    # The fixed reviewed binding is sealed; payloads cannot select a module.
    import bindings
    standing = installed_policy(installation)
    release = ImageRelease(**request['release']).validate()
    executor, public = compose(installation, standing, bindings.POLICY, release)
    scope = 'prepare-project-router-image' if request['op'] == 'prepare-cache' else 'update-project-router'
    if request['op'] == 'execute':
        candidate = OperationCandidate(**request['candidate'])
        require(candidate.scope in ('prepare-project-router-image', 'update-project-router'), 'cd-scope-not-authorized')
        executor.verifier = AutomationVerifier(standing, candidate)
        result = executor.run(candidate, base64.b64decode(request['envelope'], validate=True))
        return {'status': result.status, 'candidate': result.candidate, 'reasons': result.reasons}
    with production_host_lock() as lease:
        prepared = executor.preflight.prepare_locked(scope, lease)
        if scope == 'prepare-project-router-image' and executor.image_preparation.present(prepared):
            return {'status': 'cache-present'}
        if scope == 'update-project-router':
            from scripts.deployment import deployment_preview
            operations = deployment_preview(prepared.desired, prepared.observed, prepared.plan, 'project-router')
            if operations and all(o.action == 'no-op' for o in operations):
                public.verify(prepared.desired.applications[0])
                return {'status': 'no-op', 'image': release.image, 'manager_sha': installation.manager_sha}
        return {'status': 'prepared', 'candidate': asdict(prepared.candidate)}


def main(installation):
    from scripts.cd.diagnostics import blocked
    try:
        raw = sys.stdin.buffer.read(131073)
        request = closed_json(raw, 131072)
        result = handle(installation, request)
    except Exception as error:
        # Keep reviewed failure codes inside the canonical protocol before the
        # frozen entry's legacy fallback can mask them as invalid approvals.
        result = blocked(error, 'cd-installed-entry-failed')
    print(canonical(result).decode())
    return 0 if result['status'] in ('prepared', 'cache-present', 'no-op', 'succeeded') else 2
