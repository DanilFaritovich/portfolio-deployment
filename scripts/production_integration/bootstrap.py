"""Pure first-host preparation. Proposals never grant resource creation authority."""
from dataclasses import asdict, dataclass
import ipaddress
import math

from scripts.docker_adapter import DockerError
from scripts.production_integration.runtime import ProxyContract, validate_proxy
from scripts.reconciliation import encode
from scripts.production_integration.artifacts import blob_digest
from scripts.production_integration.network_inventory import network_subnets


@dataclass(frozen=True)
class BootstrapNetworkContract:
    subnet: str
    gateway: str
    existing_network_id: str | None = None


@dataclass(frozen=True)
class BootstrapInput:
    host: str
    daemon: str
    captured_at: float
    complete: bool
    docker: object
    listeners: tuple[int, ...]
    gateway: str  # absent / managed / foreign / unknown, independently attested
    storage: str  # absent / managed / foreign / unknown
    routes: tuple


@dataclass(frozen=True)
class BootstrapProposal:
    operations: tuple[str, ...]
    blockers: tuple[str, ...]
    rollback: str
    baseline_hash: str | None = None
    release_sequence: tuple[str, ...] = ()

    @property
    def identity(self):
        return blob_digest(encode({'operations': self.operations, 'blockers': self.blockers,
                                  'rollback': self.rollback, 'baseline': self.baseline_hash,
                                  'releaseSequence': self.release_sequence}).encode())


def plan_bootstrap(sample, *, expected_identity, proxy, private_subnet, now, attestor=None):
    """The attestor verifies exact sample/endpoint provenance, not its booleans.

    No synthesized active routes or network IDs. Before execution a future
    bootstrap must reobserve under the canonical lock and attest created IDs.
    """
    blockers, operations = set(), []
    if (not isinstance(sample, BootstrapInput) or attestor is None):
        return BootstrapProposal((), ('bootstrap-authenticated-observation-required',), 'unavailable')
    # Authenticated transport implementation is an operator-installed code port;
    # arbitrary saved inspection JSON has no path to this interface.
    if attestor.verify(sample, expected_identity) != sample:
        return BootstrapProposal((), ('bootstrap-observation-unverified',), 'unavailable')
    if ((sample.host, sample.daemon) != expected_identity or sample.complete is not True or
        not sample.docker.complete or not all(type(t) in (int, float) and math.isfinite(t) for t in (now, sample.captured_at)) or
        not 0 <= now - sample.captured_at <= 5):
        return BootstrapProposal((), ('bootstrap-stale-incomplete-or-wrong-identity',), 'unavailable')
    try:
        public = ipaddress.ip_network(proxy.subnet, strict=True)
        private = ipaddress.ip_network(private_subnet, strict=True)
        if public.version != 4 or private.version != 4 or not public.is_private or not private.is_private or public.overlaps(private):
            raise ValueError
        gateway = ipaddress.ip_address(proxy.gateway)
        if gateway not in public or gateway in (public.network_address, public.broadcast_address):
            raise ValueError
    except (ValueError, TypeError):
        return BootstrapProposal((), ('bootstrap-ipam-invalid',), 'unavailable')
    networks = [r for r in sample.docker.resources if r.kind == 'network']
    existing_proxy = [r for r in networks if r.name == 'portfolio-proxy']
    private_name = 'portfolio-project-router-private'
    for resource in sample.docker.resources:
        if resource.name == private_name or resource.name.startswith(('portfolio-gateway', 'portfolio-project-router')):
            blockers.add('bootstrap-application-or-gateway-resource-conflict')
    for network in networks:
        if network.name == 'portfolio-proxy':
            continue
        try:
            for subnet in network_subnets(network):
                if subnet.overlaps(public) or subnet.overlaps(private):
                    blockers.add('bootstrap-ipam-collision')
        except (DockerError, KeyError, ValueError, TypeError):
            blockers.add('bootstrap-network-ipam-unverified')
    if existing_proxy:
        try:
            validate_proxy(sample.docker, ProxyContract(proxy.subnet, proxy.gateway, proxy.existing_network_id))
        except DockerError:
            blockers.add('bootstrap-proxy-foreign-or-unverified')
    else:
        if proxy.existing_network_id is not None:
            blockers.add('bootstrap-expected-proxy-disappeared')
        else:
            operations.append('create-manager-proxy-with-reviewed-ipam')
    if sample.gateway != 'absent' or sample.storage != 'absent' or sample.routes:
        blockers.add('bootstrap-requires-empty-attested-gateway-and-storage')
    if set(sample.listeners) & {80, 443}:
        blockers.add('bootstrap-public-listener-conflict')
    if blockers:
        return BootstrapProposal((), tuple(sorted(blockers)), 'unavailable')
    operations.extend(('initialize-private-journal-and-storage', 'create-application-private-network',
                       'officially-validate-empty-caddy-candidate', 'create-managed-caddy-with-empty-config'))
    release = ('deploy-application-without-public-route', 'verify-application-readiness',
               'officially-validate-complete-caddy-candidate', 'activate-first-public-route',
               'verify-public-route-before-promoting-first-lkg')
    return BootstrapProposal(tuple(operations), ('stage-09-4b-bootstrap-approval-and-execution-required',),
                             'no-previous-release; preserve-data-and-require-reviewed-containment',
                             blob_digest(encode({'sample': asdict(sample), 'proxy': asdict(proxy),
                                                 'privateSubnet': private_subnet}).encode()), release)
