"""Concrete finite production recipe. Owner-selected inputs are sealed Python.

No environment/request/JSON loader selects policy, code, credentials or ports.
Administrative phase data continues to supply only its existing measured fields.
"""
from dataclasses import dataclass
from functools import partial
import hashlib
from pathlib import Path
import stat

from scripts.production_integration.administrative_review import AdministrativePhaseReview
from scripts.production_integration.artifacts import bind_engine_image, verify_oci_bytes
from scripts.production_integration.bootstrap import BootstrapNetworkContract
from scripts.production_integration.caddy_release import CaddyReleaseCollector
from scripts.production_integration.candidate import InstalledContentReader
from scripts.production_integration.composition import project_router_image_registry
from scripts.production_integration.docker import EngineContract
from scripts.production_integration.first_phase import EmptyHostReader, BootstrapResources
from scripts.production_integration.installation import protected
from scripts.production_integration.local_sources import installed_engine, LocalHostSources
from scripts.production_integration.observations import require
from scripts.production_integration.phase_bindings import build_reviewed_phase
from scripts.production_integration.public_postconditions import PublicPolicy, PublicPostconditions, tls_context
from scripts.production_integration.public_witness import SignedPublicWitness
from scripts.production_integration.release import PRODUCER, MANAGER
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GitHubRead, GHCRRead, HTTPSRead, IMAGE_REPOSITORY
from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths

TOKEN = Path('/etc/portfolio-deployment/github-read-token')
ROOT = '/etc/portfolio-deployment/sigstore-trusted-root.jsonl'
GH = '/usr/bin/gh'


@dataclass(frozen=True)
class FixedPolicy:
    public: PublicPolicy
    observer_key: str
    proxy_subnet: str
    proxy_gateway: str
    private_subnet: str
    certificate_budget_bytes: int
    caddy_source_sha: str
    caddy_image: str
    caddy_version: str
    gh_sha256: str
    sigstore_root_sha256: str
    ghcr_user: str
    producer_sha: str
    image: str
    signer_sha: str
    slsa_builder: str
    producer_checks: tuple[WorkflowCheck, ...]
    publication_check: WorkflowCheck


def build_fixed(installation, policy, *, review=None, content_reader=None, contracts=None,
                gateway_postconditions=None, composition_options=None, image_resolver=None):
    require(type(policy) is FixedPolicy, 'production-installed-bindings-unavailable-owner-policy-required')
    require(type(policy.producer_checks) is tuple and bool(policy.producer_checks) and
            all(type(c) is WorkflowCheck and c.repository == PRODUCER for c in policy.producer_checks) and
            len(set(policy.producer_checks)) == len(policy.producer_checks),
            'production-fixed-producer-checks-invalid')
    policy.public.validate()
    installation.verify()
    review = AdministrativePhaseReview(installation) if review is None else review
    require(policy.public.identity == (review.policy.host, review.policy.daemon), 'production-fixed-host-mismatch')
    pins = dict(installation.files)
    require(pins.get(GH) == policy.gh_sha256 and pins.get(ROOT) == policy.sigstore_root_sha256 and
            pins.get(policy.public.ca_file) == policy.public.ca_sha256,
            'production-fixed-external-trust-pins-missing')
    protected(TOKEN)
    require(stat.S_IMODE(TOKEN.stat().st_mode) == 0o600, 'production-fixed-token-metadata-invalid')
    with TOKEN.open('rb') as stream:
        token = stream.read(4097).decode('ascii')
    with Path(policy.public.ca_file).open('rb') as stream:
        ca = stream.read(1024 * 1024 + 1)
    require(0 < len(ca) <= 1024 * 1024 and hashlib.sha256(ca).hexdigest() == policy.public.ca_sha256,
            'production-fixed-ca-content-changed')
    context = tls_context(ca)
    # One invocation builds one executor; preserve existing finite API budgets.
    github = GitHubRead(token, HTTPSRead(context=context))
    class Registry:
        def __init__(self, repository, authenticated=False):
            self.repository, self.authenticated = repository, authenticated
        def collect(self, reference):
            return GHCRRead(HTTPSRead(context=context), repository=self.repository,
                registry_auth=(policy.ghcr_user, token) if self.authenticated else None).collect(reference)
    registry = Registry('ghcr.io/danilfaritovich/project-router')
    caddy_registry = Registry('ghcr.io/danilfaritovich/portfolio-caddy', True)
    crypto = AttestationVerifier(GH, policy.gh_sha256, ROOT, policy.sigstore_root_sha256)
    caddy_crypto = AttestationVerifier(GH, policy.gh_sha256, ROOT, policy.sigstore_root_sha256, repository=MANAGER)
    offline = WorkflowCheck(MANAGER, 'Offline checks', 15368, '.github/workflows/offline.yml')
    caddy_publication = WorkflowCheck(MANAGER, 'Publish trusted Caddy / Publish', 15368,
                                      '.github/workflows/caddy-image.yml')
    caddy = CaddyRuntime(policy.caddy_image, policy.caddy_version,
        RuntimePaths(*(f'/srv/portfolio/{p}' for p in ('state', 'locks', 'journal', 'caddy-config',
                     'certificates', 'caddy-data')), policy.certificate_budget_bytes))
    caddy.render()
    proxy = BootstrapNetworkContract(policy.proxy_subnet, policy.proxy_gateway)
    engine_contract = EngineContract('unix:///run/docker.sock', review.policy.daemon, review.policy.host, True)
    engine = installed_engine(installation, engine_contract)
    collector = CaddyReleaseCollector(github=github, registry=caddy_registry, crypto=caddy_crypto,
                                     checks=(offline,), publication_check=caddy_publication)
    reader = EmptyHostReader(installation, engine, LocalHostSources(installation, engine), review.policy, review)
    resources = BootstrapResources(installation, engine, reader, proxy, policy.private_subnet,
                                   collector, policy.caddy_source_sha, runtime_acceptance=review)
    common = dict(engine_contract=engine_contract, private_subnet=policy.private_subnet, caddy=caddy,
        producer_sha=policy.producer_sha, image=policy.image, github=github, registry=registry, crypto=crypto,
        checks=(*policy.producer_checks, offline), publication_check=policy.publication_check,
        signer_sha=policy.signer_sha, builder='https://github.com/' + PRODUCER +
        '/.github/workflows/publish.yml@' + policy.signer_sha, slsa_builder=policy.slsa_builder)
    empty = dict(common, proxy=proxy, caddy_source_sha=policy.caddy_source_sha, caddy_github=github,
        caddy_registry=caddy_registry, caddy_crypto=caddy_crypto, caddy_checks=(offline,),
        caddy_publication_check=caddy_publication)
    # The empty phase needs no images/IDs to be fabricated. Its real collector
    # verifies Caddy at bootstrap preflight. Rollout verifies it again now.
    if review.document['phase'] == 'empty-host':
        return build_reviewed_phase(installation, empty_host=empty, rollout={}, review=review)
    evidence = collector.collect(caddy, policy.caddy_source_sha)
    review.verify(caddy, evidence, engine.identity())
    contracts = InstalledContentReader(installation)()[0].applications if contracts is None else contracts
    require({a.id for a in contracts} == {'project-router'}, 'production-fixed-catalog-invalid')
    def resolve_image(reference, inspected):
        if image_resolver is not None:
            return image_resolver(reference, inspected)
        evidence = verify_oci_bytes(reference, IMAGE_REPOSITORY, registry.collect(reference))
        return bind_engine_image(evidence, inspected)
    rollout = dict(common, contracts=contracts, resolve_image=resolve_image, bootstrap_proxy=proxy,
        caddy_image=evidence, gateway_postconditions=(PublicPostconditions(policy.public,
            external=SignedPublicWitness(installation, policy.observer_key)) if gateway_postconditions is None else gateway_postconditions),
        bootstrap_reader=reader, bootstrap_attestor=reader, bootstrap_resources=resources,
        image_registry=partial(project_router_image_registry, context=context))
    rollout.update(composition_options or {})
    if content_reader is not None:
        rollout['content_reader'] = content_reader
    return build_reviewed_phase(installation, empty_host=empty, rollout=rollout, review=review)
