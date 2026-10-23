"""Fixed trusted-code wiring recipe, deliberately not installed by bindings.py.

Authorities and policies are independently reviewed installation dependencies;
none can be selected by operation JSON/environment. Missing authority blocks.
No constructors perform Engine/network access. build verifies the sealed bundle.
"""
from scripts.docker_adapter import DockerError
from scripts.gateway import desired_routes
from scripts.production_integration.admin_socket import UnixActiveChannel
from scripts.production_integration.active_runtime import PinnedCaddyRuntime
from scripts.production_integration.active_config import ActiveConfigTransport, InstalledConfigAdapter
from scripts.production_integration.bootstrap_bindings import BootstrapValidationBinding
from scripts.production_integration.candidate import CandidatePreflight, InstalledContentReader
from scripts.production_integration.execution import BootstrapExecution, ProductionExecutor
from scripts.production_integration.gateway_read import ManagedGatewayReadPort
from scripts.production_integration.gateway_write import GatewayExecutionFactory
from scripts.production_integration.host_policy import PolicyHostContracts
from scripts.production_integration.http_read import InternalHTTPRead
from scripts.production_integration.image_preparation import ImagePreparation
from scripts.production_integration.installation import SealedInstallation
from scripts.production_integration.local_sources import LocalHostSources, ReadOnlyJournalSource, installed_engine
from scripts.production_integration.mutations import ManagedBootstrapPort, ScopedProductionDocker
from scripts.production_integration.observations import ProductionStateReader
from scripts.production_integration.operator_approval import SSHSIGVerifier
from scripts.production_integration.readiness import ProductionProbes
from scripts.production_integration.release import ReleaseVerifier
from scripts.production_integration.release_collector import AuthenticatedReleaseCollector
from scripts.production_integration.release_transport import GHCRRead, HTTPSRead, IMAGE_REPOSITORY
from scripts.production_integration.runtime_sources import (RuntimeInventory, RuntimeSource, GatewaySource,
                                                            ReadinessSource, LocalSourceAttestor)
from scripts.production_integration.trust import SourceBinding, SourceTrust


def project_router_image_registry():
    """Opt-in installed factory: fresh bounded anonymous reads, fixed repository.

    Only independently reviewed bindings may select this dependency. Operation
    requests cannot select its repository, transport or credentials.
    """
    return GHCRRead(HTTPSRead(), repository=IMAGE_REPOSITORY)


def installed_active_channel(installation, engine, caddy, image, *, container_id, network_id):
    """Opt-in trusted binding recipe. Pins originate outside operation requests."""
    runtime = PinnedCaddyRuntime(installation, engine, caddy, image,
                                 container_id=container_id, network_id=network_id)
    return UnixActiveChannel(runtime), runtime


def build(installation, *, engine_contract, host_policy, prerequisites, contracts,
          infrastructure, resolve_image, proxy, bootstrap_proxy, private_subnet, private_network_id,
          caddy, caddy_image, active_channel, gateway_runtime, gateway_postconditions, bootstrap_reader,
          bootstrap_attestor, bootstrap_resources, producer_sha, image,
          github, registry, crypto, checks, publication_check, signer_sha, builder, slsa_builder,
          image_registry=None, active_pins=None):
    # This is an API for a reviewed fixed bindings.py, never a configuration loader.
    # image_registry is an independently installed bounded factory (e.g. the
    # fixed ProjectRouter factory above), separate from release collection.
    # Missing approval/wiring leaves both candidate creation and cache execution closed.
    if type(installation) is not SealedInstallation or any(x is None for x in (
        prerequisites, gateway_postconditions, bootstrap_reader, bootstrap_attestor,
        bootstrap_resources, github, registry, crypto, resolve_image)):
        raise DockerError('production-fixed-composition-authority-incomplete')
    installation.verify()
    engine = installed_engine(installation, engine_contract)
    if active_pins is not None:
        if active_channel is not None or gateway_runtime is not None or type(active_pins) is not tuple or len(active_pins) != 2:
            raise DockerError('production-active-composition-conflict')
        active_channel, gateway_runtime = installed_active_channel(installation, engine, caddy, caddy_image,
            container_id=active_pins[0], network_id=active_pins[1])
    if active_channel is None or gateway_runtime is None:
        raise DockerError('production-fixed-composition-authority-incomplete')
    content_reader = InstalledContentReader(installation)
    gateway_transport = ActiveConfigTransport(engine, active_channel, gateway_runtime,
        InstalledConfigAdapter(installation), lambda: desired_routes(content_reader()[0]))
    gateway = ManagedGatewayReadPort(gateway_transport, image_evidence=caddy_image,
                                     expected_version=caddy.version, attestor=gateway_transport)
    inventory = RuntimeInventory(engine, contracts, resolve_image, infrastructure)
    # This stage intentionally supports only the first ProjectRouter rollout.
    if not contracts or {a.id for a in contracts} != {'project-router'}:
        raise DockerError('production-fixed-composition-catalog-invalid')
    endpoints = {(c['port'], c['health']['path']) for a in contracts for c in a.spec['components'] if c['type'] != 'worker'}
    probes = ProductionProbes(engine, InternalHTTPRead(private_subnet, endpoints), 'project-router',
                              private_subnet, network_id=private_network_id)
    gateway_source = GatewaySource(engine, gateway, postconditions=gateway_postconditions)
    readiness = ReadinessSource(inventory, {'project-router': probes})
    runtime = RuntimeSource(inventory, gateway_source, readiness)
    ports = {'runtime': runtime, 'gateway': gateway_source, 'readiness': readiness}
    trust = SourceTrust(tuple(SourceBinding(name, engine_contract.host_id, engine_contract.daemon_id, port.identity)
                              for name, port in ports.items()), LocalSourceAttestor(ports))
    provider = ProductionStateReader(lambda: content_reader()[0], engine, runtime, gateway_source, readiness,
                                    resolve_image, proxy, source_trust=trust).provider()
    host = PolicyHostContracts(installation, engine, LocalHostSources(installation, engine),
                              ReadOnlyJournalSource(), host_policy, prerequisites=prerequisites)
    preflight = CandidatePreflight(manager_sha=installation.manager_sha, producer_sha=producer_sha, image=image,
        content_reader=content_reader, engine=engine, contracts=host, attestor=host, provider=provider,
        bootstrap_reader=bootstrap_reader, bootstrap_attestor=bootstrap_attestor, proxy=bootstrap_proxy,
        private_subnet=private_subnet, caddy=caddy, image_registry=image_registry)
    checks = tuple(checks)
    def release(candidate):
        collector = AuthenticatedReleaseCollector(candidate, github=github, registry=registry, crypto=crypto,
            checks=checks, publication_check=publication_check, signer_sha=signer_sha, builder=builder, slsa_builder=slsa_builder)
        return ReleaseVerifier(candidate, required_checks=[(c.repository, c.name, c.app_id) for c in checks],
                               allowed_builders=[builder], collector=collector)
    def adapter(grant):
        application = next(a for a in content_reader()[0].applications if a.id == 'project-router')
        return ScopedProductionDocker(engine_contract, engine.host_identity, installation=installation,
                                      grant=grant, application=application)
    resources = BootstrapValidationBinding(installation, caddy, resources=bootstrap_resources)
    bootstrap = ManagedBootstrapPort(installation=installation, caddy=caddy, proxy=bootstrap_proxy,
        private_subnet=private_subnet, attestor=resources, engine=engine)
    return ProductionExecutor(installation=installation, preflight=preflight,
        journal_directory='/srv/portfolio/journal', approval_verifier=SSHSIGVerifier(installation),
        release_factory=release, adapter_factory=adapter, probes=probes,
        gateway=GatewayExecutionFactory(installation, gateway, engine), bootstrap=BootstrapExecution(bootstrap),
        image_preparation=(ImagePreparation(installation=installation, engine=engine)
                           if image_registry is not None else None))
