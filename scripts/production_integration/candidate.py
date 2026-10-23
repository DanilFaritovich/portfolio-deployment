"""Read -> independent attestation -> deterministic candidate; no approval I/O."""
from dataclasses import asdict, dataclass
import math
import time

from scripts.compose import render_compose
from scripts.deployment import deployment_preview, preflight_resources
from scripts.docker_adapter import DockerError
from scripts.gateway import build_gateway_candidate
from scripts.production_integration.artifacts import blob_digest
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.bootstrap import plan_bootstrap
from scripts.reconciliation import build_reconciliation_plan, encode


def hashed(value):
    return blob_digest(encode(value).encode())


@dataclass(frozen=True)
class HostContracts:
    """Exact installed policy AND fresh observations, independently attested.

    network includes host/VPN routes, reviewed IPAM and conflicts; storage includes
    paths, ownership, budgets, capacity and backup evidence; recovery includes
    unfinished journal/ledger inventory and the approved containment/restore plan.
    These canonical JSON payloads are never accepted from an inspection report.
    """
    host: str
    daemon: str
    captured_at: float
    network: str
    storage: str
    recovery: str
    bootstrap_verified: bool


@dataclass(frozen=True)
class PreparedOperation:
    candidate: OperationCandidate
    desired: object
    observed: object
    plan: object
    bootstrap: object = None
    resource_contract: str | None = None
    image_evidence: object = None


class CandidatePreflight:
    """All ports are installed trusted code. Defaults cannot mint a candidate.

    contracts.collect() performs fresh read-only collection; attestor.verify()
    must independently verify its exact payload/channel and return that object.
    For rollout the provider MUST be the installed ProductionStateReader provider
    with SourceTrust; installation wiring, never request JSON, selects these ports.
    """
    def __init__(self, *, manager_sha, producer_sha, image, content_reader, engine,
                 contracts, attestor=None, provider=None, bootstrap_reader=None,
                 bootstrap_attestor=None, proxy=None, private_subnet=None,
                 caddy=None, image_registry=None, clock=time.monotonic):
        self.manager_sha, self.producer_sha, self.image = manager_sha, producer_sha, image
        self.content_reader, self.engine = content_reader, engine
        self.contracts, self.attestor, self.provider = contracts, attestor, provider
        self.bootstrap_reader, self.bootstrap_attestor = bootstrap_reader, bootstrap_attestor
        self.proxy, self.private_subnet, self.caddy, self.clock = proxy, private_subnet, caddy, clock
        self.image_registry = image_registry

    def prepare(self, scope):
        if self.attestor is None:
            raise DockerError('production-host-contract-attestor-unavailable')
        image_evidence = None
        if scope == 'prepare-project-router-image':
            if self.image_registry is None:
                raise DockerError('production-image-registry-unavailable')
            from scripts.production_integration.artifacts import verify_oci_bytes
            from scripts.production_integration.release_transport import IMAGE_REPOSITORY
            image_evidence = verify_oci_bytes(self.image, IMAGE_REPOSITORY,
                                              self.image_registry().collect(self.image))
        start = self.clock()
        identity = self.engine.identity()
        contracts = self.contracts.collect()
        now = self.clock()
        if (type(contracts) is not HostContracts or
            self.attestor.verify(contracts, identity) != contracts or
            (contracts.host, contracts.daemon) != identity or
            not all(type(t) in (int, float) and math.isfinite(t) for t in (start, now, contracts.captured_at)) or
            not start <= contracts.captured_at <= now or now - start > 5):
            raise DockerError('production-host-contract-unverified')
        from scripts.planning_inputs import parse_snapshot
        extra = []
        for raw in (contracts.network, contracts.storage, contracts.recovery):
            doc = parse_snapshot(raw.encode())
            if not isinstance(doc, dict) or not doc:
                raise DockerError('production-host-contract-incomplete')
            extra.append(doc)
        # The reader re-admits the registry, checks installed manager revision and
        # returns exact raw selected manifest/policy bytes from the sealed release.
        desired, manifest, policy = self.content_reader()
        if desired.git_revision != self.manager_sha or type(manifest) is not bytes or type(policy) is not bytes or not manifest or not policy:
            raise DockerError('production-manager-content-unverified')
        bootstrap = None
        if scope == 'prepare-project-router-image':
            plan_doc = {'operation': scope, 'image': self.image, 'platform': 'linux/amd64'}
            runtime = asdict(image_evidence)
            gateway = {'activation': False}
            observed, plan = None, None
        elif scope == 'first-bootstrap':
            if self.bootstrap_reader is None or self.caddy is None:
                raise DockerError('production-bootstrap-dependencies-unavailable')
            sample = self.bootstrap_reader.collect()
            bootstrap = plan_bootstrap(sample, expected_identity=identity, proxy=self.proxy,
                                       private_subnet=self.private_subnet, now=self.clock(),
                                       attestor=self.bootstrap_attestor)
            if (bootstrap.blockers != ('stage-09-4b-bootstrap-approval-and-execution-required',) or not bootstrap.operations):
                raise DockerError('production-bootstrap-preflight-blocked')
            # Capture time is freshness evidence, not a state identity. Hash only
            # the independently authenticated facts, preserving every resource ID.
            baseline = asdict(sample)
            baseline.pop('captured_at')
            plan_doc = {'operations': bootstrap.operations, 'baseline': baseline,
                        'proxy': asdict(self.proxy), 'privateSubnet': self.private_subnet}
            runtime = self.caddy.render().model
            gateway = {'runtime': runtime, 'routes': []}
            observed, plan = None, None
        elif scope == 'first-project-router-rollout':
            if self.provider is None or contracts.bootstrap_verified is not True:
                raise DockerError('production-verified-bootstrap-required')
            fresh, observed, docker = self.provider.refresh()
            if fresh != desired or len(desired.applications) != 1 or desired.applications[0].id != 'project-router':
                raise DockerError('production-desired-scope-mismatch')
            app = desired.applications[0]
            if len(app.spec['components']) != 1 or app.spec['components'][0]['image'] != self.image:
                raise DockerError('production-release-image-mismatch')
            if any(a['id'] == app.id for a in observed.document['applications']):
                raise DockerError('production-first-rollout-already-present')
            plan = build_reconciliation_plan(desired, observed)
            operations = deployment_preview(desired, observed, plan, app.id)
            if (any(o.eligibility == 'blocked' or o.action != 'create' for o in operations) or
                preflight_resources(app, render_compose(app), docker)):
                raise DockerError('production-plan-preflight-blocked')
            runtime = {'compose': render_compose(app).model, 'docker': asdict(docker), 'observed': observed.document}
            gateway = build_gateway_candidate(desired, observed, plan).document
            if gateway['status'] in ('blocked', 'degraded'):
                raise DockerError('production-gateway-preflight-blocked')
            plan_doc = plan.document
        else:
            raise DockerError('production-operation-scope-invalid')
        if identity != self.engine.identity() or self.clock() - start > 5:
            raise DockerError('production-preflight-state-changed')
        candidate = OperationCandidate(*identity, self.manager_sha, self.producer_sha, self.image,
                                       blob_digest(manifest), blob_digest(policy), hashed(plan_doc), hashed(gateway),
                                       hashed(runtime), hashed(extra[0]), hashed(extra[1]), hashed(extra[2]), scope)
        candidate.validate()
        return PreparedOperation(candidate, desired, observed, plan, bootstrap,
                                 encode(plan_doc) if bootstrap is not None else None, image_evidence)


class InstalledContentReader:
    """Fixed sealed release registry; reuses Stage 02 admission on every call."""
    def __init__(self, installation):
        self.installation = installation

    def __call__(self):
        from scripts.planning_inputs import build_desired_state
        from scripts.production_integration.installation import BUNDLE
        self.installation.verify()
        root = BUNDLE / 'manager'
        desired = build_desired_state(root, self.installation.manager_sha)
        return (desired, (root / 'registry/applications/project-router.yaml').read_bytes(),
                (root / 'policy/admission.yaml').read_bytes())
