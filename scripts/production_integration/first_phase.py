"""Fixed empty-host composition. Installed code selects authorities, never requests.

No rollout ports or preexisting runtime IDs. The shipped binding remains closed
until the external policies and authenticated authorities receive owner review.
"""
from dataclasses import asdict
import ipaddress
from pathlib import Path
import re
import time

from scripts.compose import labels
from scripts.docker_discovery import discover
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.active_config import ActiveConfigTransport, InstalledConfigAdapter
from scripts.production_integration.active_runtime import PinnedCaddyRuntime
from scripts.production_integration.admin_socket import UnixActiveChannel
from scripts.production_integration.bootstrap import BootstrapInput, BootstrapNetworkContract
from scripts.production_integration.bootstrap_bindings import BootstrapValidationBinding
from scripts.production_integration.caddy_image import bind_caddy_engine
from scripts.production_integration.caddy_release import CaddyReleaseCollector
from scripts.production_integration.candidate import CandidatePreflight, InstalledContentReader, hashed
from scripts.production_integration.docker import EngineContract
from scripts.production_integration.execution import BootstrapExecution, ProductionExecutor
from scripts.production_integration.gateway import content
from scripts.production_integration.host_policy import (HostPolicy, PolicyHostContracts, network_facts,
                                                       storage_contents, validate_network_inventory)
from scripts.production_integration.installation import SealedInstallation
from scripts.production_integration.local_sources import LocalHostSources, ReadOnlyJournalSource, installed_engine
from scripts.production_integration.mutations import ManagedBootstrapPort
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import SSHSIGVerifier
from scripts.production_integration.release import ReleaseVerifier
from scripts.production_integration.release_collector import AuthenticatedReleaseCollector
from scripts.production_integration.runtime import ProxyContract, validate_proxy
from scripts.readiness import ReadinessEvidence


class EmptyHostReader:
    """Fresh local facts authenticated against the independent phase HostPolicy.

    The same object implements the sample attestor: it authenticates only its last
    in-process payload, exact policy and Engine. It does not issue prerequisites.
    """
    def __init__(self, installation, engine, local, policy, prerequisites, *,
                 contents=storage_contents, clock=time.monotonic):
        self.installation, self.engine, self.local = installation, engine, local
        self.policy, self.prerequisites, self.contents, self.clock = policy, prerequisites, contents, clock
        self.last = None

    def collect(self):
        self.last = None
        self.installation.verify()
        start, identity = self.clock(), self.engine.identity()
        p = self.policy
        require(type(p) is HostPolicy and identity == (p.host, p.daemon),
                'production-bootstrap-policy-identity-mismatch')
        require(self.prerequisites is not None and self.prerequisites.verify(p, identity) == p,
                'production-bootstrap-prerequisites-unverified')
        host, docker, contents = self.local.collect(), discover(self.engine, ()), self.contents()
        require(docker.complete and host.identity == identity, 'production-bootstrap-inventory-incomplete')
        validate_network_inventory(docker)
        require(not any(r.kind in ('container', 'volume') for r in docker.resources),
                'production-bootstrap-empty-engine-required')
        require(hashed(network_facts(host, docker)) == p.network_hash and
                hashed(asdict(docker)) == p.resources_hash and hashed(contents) == p.storage_hash,
                'production-bootstrap-phase-policy-mismatch')
        # Installed directories/lock/journal are allowed; gateway runtime data is
        # genuinely absent, not inferred from missing privileges or a marker.
        empty = ('caddy-config', 'caddy-lkg', 'certificates', 'caddy-data', 'caddy-admin')
        for name in empty:
            require(not any(Path('/srv/portfolio', name).iterdir()),
                    'production-bootstrap-runtime-storage-not-empty')
        require(discover(self.engine, ()) == docker and self.contents() == contents and
                self.engine.identity() == identity and start <= host.captured_at <= self.clock() and
                0 <= self.clock() - start <= 5, 'production-bootstrap-facts-changed-or-expired')
        sample = BootstrapInput(*identity, self.clock(), True, docker,
            tuple(r['port'] for r in host.network['listeners']), 'absent', 'absent', ())
        self.last = (sample, hashed(asdict(sample)), p.identity)
        return sample

    def verify(self, sample, identity):
        require(self.last is not None and sample is self.last[0] and
                hashed(asdict(sample)) == self.last[1] and self.policy.identity == self.last[2] and
                identity == (sample.host, sample.daemon) == self.engine.identity() and
                0 <= self.clock() - sample.captured_at <= 5,
                'production-bootstrap-observation-unbound-or-expired')
        return sample


class BootstrapResources:
    """Real resource checks; protected-source Caddy collector remains mandatory.

    New IDs are observed only after creation and never adopted as rollout pins.
    Two complete stable checks precede journal promotion. Failure preserves data.
    """
    def __init__(self, installation, engine, reader, proxy, private_subnet, caddy_release, source_sha,
                 *, runtime_acceptance=None, clock=time.monotonic):
        self.installation, self.engine, self.reader = installation, engine, reader
        self.proxy, self.private_subnet = proxy, private_subnet
        self.release, self.source_sha, self.clock = caddy_release, source_sha, clock
        self.runtime_acceptance = runtime_acceptance
        self.prepared = None
        self.evidence = None
        self.runtime = None

    def preflight(self, prepared, runtime):
        self.prepared = self.evidence = self.runtime = None
        self.installation.verify()
        require(type(self.release) is CaddyReleaseCollector, 'production-bootstrap-caddy-authority-required')
        sample = self.reader.collect()
        self.reader.verify(sample, (prepared.candidate.host, prepared.candidate.daemon))
        baseline = asdict(sample)
        baseline.pop('captured_at')
        contract = parse_snapshot(prepared.resource_contract.encode())
        require(prepared.candidate.scope == 'first-bootstrap' and
                hashed(contract) == hashed({'operations': prepared.bootstrap.operations, 'baseline': baseline,
                             'proxy': asdict(self.proxy), 'privateSubnet': self.private_subnet}) and
                hashed(contract) == prepared.candidate.plan_hash and
                hashed(runtime.render().model) == prepared.candidate.runtime_hash,
                'production-bootstrap-precondition-changed')
        evidence = self.release.collect(runtime, self.source_sha)
        require(self.runtime_acceptance is not None and
                self.runtime_acceptance.verify(runtime, evidence, self.engine.identity()) is evidence,
                'production-bootstrap-runtime-acceptance-unverified')
        bind_caddy_engine(runtime, evidence, self.engine.inspect('image', runtime.image))
        self.prepared, self.evidence, self.runtime = prepared, evidence, runtime
        return prepared

    def _check_created(self, prepared, runtime):
        identity = self.engine.identity()
        require(identity == (prepared.candidate.host, prepared.candidate.daemon),
                'production-bootstrap-created-identity-mismatch')
        host = self.reader.local.collect()
        require(host.identity == identity and all(r['availableBytes'] >= self.reader.policy.minimum_bytes and
                r['availableInodes'] >= self.reader.policy.minimum_inodes for r in host.storage),
                'production-bootstrap-created-capacity-insufficient')
        storage_contents()  # bounded metadata/content checks, including the new UDS
        target = Path(runtime.paths.gateway_config) / 'Caddyfile'
        expected = content(()).encode()
        with target.open('rb') as stream:
            require(stream.read(len(expected) + 1) == expected, 'production-bootstrap-created-config-mismatch')
        docker = discover(self.engine, ())
        require(docker.complete, 'production-bootstrap-created-inventory-incomplete')
        proxy = [r for r in docker.resources if r.kind == 'network' and r.name == 'portfolio-proxy']
        private = [r for r in docker.resources if r.kind == 'network' and r.name == 'portfolio-project-router-private']
        containers = [r for r in docker.resources if r.kind == 'container']
        require(len(proxy) == len(private) == len(containers) == 1,
                'production-bootstrap-created-resources-missing-or-foreign')
        net, priv, container = proxy[0], private[0], containers[0]
        owner = container.facts.get('labels', {})
        require(container.name == 'portfolio-gateway-caddy-1' and
                owner.get('com.docker.compose.project') == 'portfolio-gateway' and
                owner.get('com.docker.compose.service') == 'caddy',
                'production-bootstrap-created-compose-identity-mismatch')
        require(all(re.fullmatch(r'[0-9a-f]{64}', r.identifier) for r in (net, priv, container)),
                'production-bootstrap-created-id-invalid')
        validate_proxy(docker, ProxyContract(self.proxy.subnet, self.proxy.gateway, net.identifier))
        f = priv.facts
        subnet = ipaddress.ip_network(self.private_subnet, strict=True)
        require(f['driver'] == 'bridge' and f['scope'] == 'local' and f['internal'] is True and
                f['options'] == {} and f['containers'] == {} and f['attachable'] is False and
                f['ingress'] is False and f['enableIPv6'] is False and f['labels'] == {
                    **labels('project-router'), 'com.docker.compose.project': 'portfolio-project-router',
                    'com.docker.compose.network': 'private'} and f['ipam'] == {
                    'Driver': 'default', 'Options': {}, 'Config': [
                        {'Subnet': self.private_subnet, 'Gateway': str(subnet.network_address + 1)}]},
                'production-bootstrap-private-network-mismatch')
        require(set(net.facts['containers']) == {container.identifier},
                'production-bootstrap-proxy-membership-mismatch')
        baseline = parse_snapshot(prepared.resource_contract.encode())['baseline']['docker']['resources']
        retained = [asdict(r) for r in docker.resources if r not in (net, priv, container)]
        require(retained == baseline, 'production-bootstrap-foreign-resources-changed')
        # Existing pinned runtime/UDS/ACTIVE code authenticates these newly
        # observed IDs, exact image, storage, socket peer PID and empty config.
        pinned = PinnedCaddyRuntime(self.installation, self.engine, runtime, self.evidence,
                                   container_id=container.identifier, network_id=net.identifier)
        channel = UnixActiveChannel(pinned)
        transport = ActiveConfigTransport(self.engine, channel, pinned, InstalledConfigAdapter(self.installation), lambda: ())
        sample = transport.read_active()
        transport.verify(transport, self.evidence, runtime.version, sample)
        require(sample.routes == () and sample.active_caddyfile == content(()) and
                self.engine.identity() == identity, 'production-bootstrap-empty-active-unverified')
        require(discover(self.engine, ()) == docker, 'production-bootstrap-created-inventory-changed')
        return (net.identifier, priv.identifier, container.identifier, sample.channel_identity)

    def verify_created(self, prepared, runtime):
        require(prepared is self.prepared and self.evidence is not None and runtime is self.runtime,
                'production-bootstrap-preflight-required')
        self.installation.verify()
        start = self.clock()
        first = self._check_created(prepared, runtime)
        second = self._check_created(prepared, runtime)
        require(first == second and 0 <= self.clock() - start <= 15,
                'production-bootstrap-postconditions-changed-or-expired')
        return ReadinessEvidence('verified', 'readiness-verified', 2, 2)


def build_first_phase(installation, *, engine_contract, host_policy, prerequisites, proxy, private_subnet,
                      caddy, caddy_source_sha, caddy_github, caddy_registry, caddy_crypto,
                      caddy_checks, caddy_publication_check, caddy_acceptance, producer_sha, image,
                      github, registry, crypto, checks, publication_check, signer_sha, builder, slsa_builder):
    """Reviewed Python-only composition; no configuration/deserialization API.

    External authorities are explicit mandatory dependencies. Full rollout stays
    unavailable until a separately sealed composition reviews post-bootstrap IDs.
    """
    require(type(installation) is SealedInstallation and type(engine_contract) is EngineContract and
            engine_contract.endpoint == 'unix:///run/docker.sock' and engine_contract.read_authorized is True and
            type(host_policy) is HostPolicy and (host_policy.host, host_policy.daemon) ==
            (engine_contract.host_id, engine_contract.daemon_id) and
            type(proxy) is BootstrapNetworkContract and proxy.existing_network_id is None and
            all(x is not None for x in (prerequisites, caddy_github, caddy_registry, caddy_crypto, caddy_acceptance, github, registry, crypto)),
            'production-first-phase-policy-or-authority-incomplete')
    require((caddy.paths.state, caddy.paths.lock, caddy.paths.journal, caddy.paths.gateway_config,
             caddy.paths.certificate_data, caddy.paths.gateway_data, caddy.paths.admin_socket_directory) ==
            tuple('/srv/portfolio/' + p for p in ('state', 'locks', 'journal', 'caddy-config',
                                                'certificates', 'caddy-data', 'caddy-admin')),
            'production-first-phase-fixed-storage-required')
    caddy.render()
    installation.verify()
    engine = installed_engine(installation, engine_contract)
    local = LocalHostSources(installation, engine)
    host = PolicyHostContracts(installation, engine, local, ReadOnlyJournalSource(), host_policy,
                               prerequisites=prerequisites)
    reader = EmptyHostReader(installation, engine, local, host_policy, prerequisites)
    resources = BootstrapResources(installation, engine, reader, proxy, private_subnet,
        CaddyReleaseCollector(github=caddy_github, registry=caddy_registry, crypto=caddy_crypto,
                             checks=caddy_checks, publication_check=caddy_publication_check), caddy_source_sha, runtime_acceptance=caddy_acceptance)
    preflight = CandidatePreflight(manager_sha=installation.manager_sha, producer_sha=producer_sha, image=image,
        content_reader=InstalledContentReader(installation), engine=engine, contracts=host, attestor=host,
        bootstrap_reader=reader, bootstrap_attestor=reader, proxy=proxy, private_subnet=private_subnet, caddy=caddy)
    checks = tuple(checks)
    def release(candidate):
        collector = AuthenticatedReleaseCollector(candidate, github=github, registry=registry, crypto=crypto,
            checks=checks, publication_check=publication_check, signer_sha=signer_sha, builder=builder, slsa_builder=slsa_builder)
        return ReleaseVerifier(candidate, required_checks=[(c.repository, c.name, c.app_id) for c in checks],
                               allowed_builders=[builder], collector=collector)
    bootstrap = ManagedBootstrapPort(installation=installation, caddy=caddy, proxy=proxy, private_subnet=private_subnet,
        engine=engine, attestor=BootstrapValidationBinding(installation, caddy, resources=resources))
    return ProductionExecutor(installation=installation, preflight=preflight, journal_directory='/srv/portfolio/journal',
        approval_verifier=SSHSIGVerifier(installation), release_factory=release, bootstrap=BootstrapExecution(bootstrap))
