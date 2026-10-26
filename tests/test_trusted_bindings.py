"""B2 acceptance: controlled I/O, real sources/coordinator/SSHSIG/flock/SQLite.

Synthetic Engine/filesystem/administrative authorities, never production evidence.
Only temporary ssh-keygen subprocesses are allowed; no sockets/Docker/SSH/VM.
"""
from dataclasses import asdict, replace
import copy
import json
from types import SimpleNamespace

import pytest

from scripts.compose import LABEL, render_compose
from scripts.deployment import owns_host_lock
from scripts.deployment_journal import Journal
from scripts.docker_adapter import DockerError
from scripts.gateway import desired_routes
from scripts.production_integration.active_config import ActiveConfigTransport, UnconfiguredActiveChannel, active_json
from scripts.production_integration.candidate import hashed
from scripts.production_integration.gateway_read import GatewaySnapshot, ManagedGatewayReadPort
from scripts.production_integration.host_policy import HostPolicy, PolicyHostContracts, network_facts
from scripts.production_integration.http_read import InternalHTTPRead
from scripts.production_integration.local_sources import LocalHostFacts, PATHS, ReadOnlyJournalSource
from scripts.production_integration.observations import ProductionStateReader
from scripts.production_integration.readiness import ProductionProbes
from scripts.production_integration.runtime_sources import (RuntimeInventory, RuntimeSource, GatewaySource,
    ReadinessSource, LocalSourceAttestor, infrastructure_key)
from scripts.production_integration.trust import SourceBinding, SourceTrust
from scripts.reconciliation import encode
from test_production_contracts import registration
from test_production_integration import router, proxy
from test_production_execution import setup
from test_operator_approval import signer, verifier, local_crypto_only

CID, NID = 'e'*64, '7'*64


class Engine:
    """Controlled complete Engine read observations, no alternate planner logic."""
    def __init__(self, world):
        self.world, self.failure, self.identity_value = world, None, ('a'*32, 'daemon')
        self.version = 0

    def identity(self):
        return self.identity_value

    def inventory(self, kind):
        if self.failure == 'denied':
            raise DockerError('docker-permission-denied')
        if self.failure == 'incomplete' and kind == 'volume':
            raise DockerError('inventory-limit')
        if kind == 'volume': return ()
        if kind == 'container':
            records = [self.inspect('container', CID)] if self.world.present else []
            if self.failure == 'foreign':
                records.append({'Id': 'f'*64, 'Name': '/foreign', 'Config': {'Labels': {}},
                                'HostConfig': {}, 'NetworkSettings': {}, 'State': {}})
            return tuple(records)
        pc, resource = proxy()
        return ({'Id': pc.network_id, 'Name': 'portfolio-proxy', 'Labels': resource.facts['labels'],
                 'Driver': 'bridge', 'Scope': 'local', 'Internal': False, 'Options': {}, 'Attachable': False,
                 'Ingress': False, 'EnableIPv6': False, 'IPAM': resource.facts['ipam'], 'Containers': {}},
                self.inspect('network', NID))

    def inspect(self, kind, identity):
        if kind == 'image':
            image = self.world.image
            return {'Id': image.image_id, 'RepoDigests': [image.reference], 'Os': 'linux', 'Architecture': 'amd64',
                    'Config': json.loads(image.defaults_json)}
        if kind == 'network':
            artifact = render_compose(self.world.app)
            private = artifact.model['networks']['private']
            return {'Id': NID, 'Name': private['name'], 'Labels': {**private['labels'],
                    'com.docker.compose.project': artifact.project, 'com.docker.compose.network': 'private'},
                    'Driver': 'bridge', 'Scope': 'local', 'Internal': True, 'EnableIPv6': False, 'Options': {},
                    'IPAM': {'Driver': 'default', 'Options': {}, 'Config': [{'Subnet': '172.31.0.0/24'}]},
                    'Containers': {CID: {'IPv4Address': '172.31.0.2/24'}} if self.world.present else {}}
        f = self.world.container.facts
        networks = copy.deepcopy(f['networks'])
        networks['portfolio-project-router-private'].update(IPAddress='172.31.0.2', NetworkID=NID)
        return {'Id': CID, 'Name': '/' + self.world.container.name, 'Image': f['imageId'], 'State': {'Status': 'running'},
                'Config': {**f['config'], 'Labels': f['labels']}, 'HostConfig': f['host'],
                'NetworkSettings': {'Networks': networks}, 'Mounts': f['mounts']}


@pytest.fixture
def integrated(setup, verifier, monkeypatch):
    s = setup
    from scripts.production_integration.artifacts import blob_digest, verify_oci_bytes, bind_engine_image
    from scripts.reconciliation import DesiredState
    config = encode({'os': 'linux', 'architecture': 'amd64', 'config': json.loads(s.world.image.defaults_json)}).encode()
    manifest = encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': blob_digest(config), 'size': len(config)},
        'layers': []}).encode()
    s.blobs = {blob_digest(raw): raw for raw in (config, manifest)}
    reference = 'ghcr.io/danilfaritovich/project-router@' + blob_digest(manifest)
    spec = s.world.app.spec
    spec['components'][0]['image'] = reference
    s.world.app = replace(s.world.app, spec_json=encode(spec))
    s.world.desired = DesiredState(s.world.desired.git_revision, (s.world.app,))
    s.world.image = verify_oci_bytes(reference, 'ghcr.io/danilfaritovich/project-router', s.blobs)
    facts = s.world.container.facts
    facts['config']['Image'], facts['imageId'] = reference, s.world.image.image_id
    facts['labels'].update(render_compose(s.world.app).model['services']['web']['labels'])
    s.world.container = replace(s.world.container, facts_json=encode(facts))
    s.preflight.image = reference
    resolve = lambda ref, raw: bind_engine_image(verify_oci_bytes(ref, ref.split('@')[0], s.blobs), raw)
    s.engine = engine = Engine(s.world)
    s.http_status = 200
    s.exchange = lambda *args: s.http_status
    http = InternalHTTPRead('172.31.0.0/24', {(8080, '/en/')}, exchange=lambda *args:s.exchange(*args))
    probes = ProductionProbes(engine, http, 'project-router', '172.31.0.0/24', network_id=NID)
    s.probes = probes
    routes = lambda: desired_routes(s.world.desired) if s.world.route else ()
    image = s.world.image
    runtime = SimpleNamespace(inspect=lambda channel: GatewaySnapshot((), '', image.image_id, '2.11.7', True, True, True, 'b'*64))
    s.active_failure = None
    s.active_reads = 0
    def active(container_id, *, timeout, limit):
        assert container_id == 'b'*64 and timeout == 1 and limit == 1048576
        s.active_reads += 1
        if s.active_failure == 'unavailable': raise DockerError('gateway-channel-unavailable')
        if s.active_failure == 'altered': return b'{"foreign":true}'
        return encode({'routes': [r.key for r in routes()]}).encode()
    channel = SimpleNamespace(read_active=active)
    adapter = SimpleNamespace(adapt=lambda rs: encode({'routes': [r.key for r in rs]}).encode())
    transport = ActiveConfigTransport(engine, channel, runtime, adapter, lambda:desired_routes(s.world.desired), clock=lambda:0)
    gateway = ManagedGatewayReadPort(transport, image_evidence=image, expected_version='2.11.7', attestor=transport)
    inventory = RuntimeInventory(engine, (s.world.app,), resolve, ())
    from scripts.docker_discovery import discover
    docker = discover(engine, (), resolve_image=lambda ref, raw:image)
    inventory.infrastructure = frozenset(infrastructure_key(r) for r in docker.resources if r.name == 'portfolio-proxy')
    gs = GatewaySource(engine, gateway, postconditions=SimpleNamespace(verify=lambda routes, identity:routes), clock=lambda:0)
    rs = ReadinessSource(inventory, {'project-router': probes}, clock=lambda:0)
    source = RuntimeSource(inventory, gs, rs, clock=lambda:0)
    ports = {'runtime': source, 'gateway': gs, 'readiness': rs}
    trust = SourceTrust(tuple(SourceBinding(n, *engine.identity(), p.identity) for n,p in ports.items()), LocalSourceAttestor(ports))
    provider = ProductionStateReader(lambda:s.world.desired, engine, source, gs, rs,
        resolve, proxy()[0], clock=lambda:0, source_trust=trust).provider()
    s.inventory, s.ports, s.provider = inventory, ports, provider
    s.preflight.provider = provider
    s.preflight.engine = engine
    s.executor.probes = probes
    s.executor.verifier = verifier
    # Exercise the real collector and source policy; only API/registry/crypto
    # transports are controlled. Synthetic crypto output proves no live provenance.
    from test_release_collector import fixture
    from scripts.production_integration.release import ReleaseVerifier, PRODUCER, MANAGER
    from scripts.production_integration.release_collector import AuthenticatedReleaseCollector, SLSA, BUILD_ORIGIN
    old, docs, _, _, release_verifier, _ = fixture()
    replacements = {old.manager_sha:s.preflight.manager_sha, old.producer_sha:s.preflight.producer_sha,
                    old.image:reference}
    def replace_strings(value):
        raw = json.dumps(value)
        for before, after in replacements.items(): raw = raw.replace(before, after)
        return json.loads(raw)
    updated = {(repo, replace_strings(path)): replace_strings(doc) for (repo,path),doc in docs.items()}
    # Attestation endpoint uses the digest alone.
    updated[PRODUCER, '/attestations/'+reference.split('@')[1]+'?per_page=100'] = {'attestations':[{'bundle':{'synthetic':True}}]}
    original = release_verifier.collector
    builder = original.builder.replace(old.producer_sha, s.preflight.producer_sha)
    def crypto(subject, ref, identity, expectations, bundles):
        assert ref == reference and blob_digest(subject) == ref.split('@')[1]
        return ({'predicateType': SLSA, 'predicate': {'runDetails': {'builder': {'id': original.slsa_builder}}}},
                {'predicateType': BUILD_ORIGIN, 'predicate': expectations[1]['expectedPredicate']})
    s.release_arguments = dict(
        github=SimpleNamespace(get=lambda repo,path:copy.deepcopy(updated[repo,path])),
        registry=SimpleNamespace(collect=lambda ref:s.blobs), crypto=SimpleNamespace(verify=crypto),
        checks=original.checks, publication_check=original.publication_check,
        signer_sha=s.preflight.producer_sha, builder=builder, slsa_builder=original.slsa_builder)
    def release(candidate):
        collector = AuthenticatedReleaseCollector(candidate, **s.release_arguments)
        return ReleaseVerifier(candidate, required_checks=release_verifier.required_checks,
                               allowed_builders=[builder], collector=collector)
    s.executor.release_factory = release
    # Real read-only SQLite reader against the same temporary durable journal.
    from scripts.production_integration import local_sources
    monkeypatch.setattr(local_sources, 'JOURNAL_DIRECTORY', str(s.directory))
    monkeypatch.setattr(local_sources, 'validate_storage_directory', lambda *a, **kw:None)
    monkeypatch.setattr(local_sources, 'protected', lambda *a, **kw:None)
    def require(lease):
        if not owns_host_lock(lease): raise DockerError('lease-required')
    monkeypatch.setattr(local_sources, 'require_production_lease', require)
    storage = tuple(dict(path=p, uid=0, gid=0, mode=0o700, availableBytes=2**32, availableInodes=10000) for p in PATHS)
    s.facts = LocalHostFacts(engine.identity(), 0, {'routes4': [], 'routes6': [], 'rules4': [], 'rules6': [], 'listeners': []}, storage)
    s.contents = ({'path':'/srv/portfolio', 'owner':'independent-test-owner'},)
    s.policy = HostPolicy(*engine.identity(), hashed(network_facts(s.facts, docker)), hashed(asdict(docker)),
        hashed(s.contents), 2**30, 1000, 'test-restore', 'test-console', 'test-ipam', 'test-budget', 'retain-and-investigate')
    s.authority = SimpleNamespace(verify=lambda policy, identity:policy)
    def collect_host(*, before_observation):
        before_observation()
        return s.facts
    host = PolicyHostContracts(s.executor.installation, engine, SimpleNamespace(collect=collect_host),
        ReadOnlyJournalSource(), s.policy, prerequisites=s.authority, contents=lambda:s.contents, clock=lambda:0)
    s.host = host
    s.preflight.contracts = s.preflight.attestor = host
    def prepare():
        with s.lock() as lease: return s.preflight.prepare_locked('first-project-router-rollout', lease).candidate
    s.prepare = prepare
    return s


def signed(s, signer, candidate=None, nonce='1'*32):
    c = candidate or s.prepare()
    return c, signer.sign(dict(version=1, candidate=c.identity, operator='operator', nonce=nonce, not_before=90, expires=150))


def test_full_sources_signed_approval_executor_and_postconditions(integrated, signer):
    s = integrated
    c, envelope = signed(s, signer)
    assert s.prepare() == c
    result = s.executor.run(c, envelope)
    s.provider.refresh()
    assert result.status == 'succeeded', result
    assert s.world.calls == [('up', 'portfolio-project-router', 'web'), ('gateway',)]
    desired, observed, docker = s.provider.refresh()
    assert observed.document['applications'][0]['components'][0]['health'] == 'healthy'
    assert any(r.identifier == CID for r in docker.resources)
    assert s.active_reads >= 4
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web')['run'] == '1'*32
    assert s.executor.run(c, envelope).status == 'blocked'
    assert len(s.world.calls) == 2


@pytest.mark.parametrize('failure', ['denied','incomplete','foreign','daemon','machine','altered','unavailable',
    'storage','ipam','backup','console','authority','verifier','signature','candidate','journal'])
def test_preflight_failures_have_zero_mutations(integrated, signer, failure):
    s = integrated
    c, envelope = signed(s, signer)
    if failure in ('denied','incomplete','foreign'): s.engine.failure = failure
    elif failure == 'daemon': s.engine.identity_value = ('a'*32, 'changed')
    elif failure == 'machine': s.engine.identity_value = ('b'*32, 'daemon')
    elif failure in ('altered','unavailable'): s.active_failure = failure
    elif failure == 'storage': s.contents = ({'path':'foreign'},)
    elif failure == 'ipam': s.facts.network['routes4'].append({'dst':'172.31.0.0/24', 'dev':'vpn'})
    elif failure in ('backup','console'):
        s.host.policy = replace(s.policy, **{'backup_restore' if failure == 'backup' else 'console_recovery': ''})
    elif failure == 'authority': s.host.prerequisites = None
    elif failure == 'verifier': s.executor.verifier = None
    elif failure == 'signature':
        doc = json.loads(envelope); doc['payload']['nonce'] = '2'*32
        from scripts.production_integration.operator_approval import canonical
        envelope = canonical(doc)
    elif failure == 'candidate': s.preflight.producer_sha = 'f'*40
    else:
        (s.directory/'journal.sqlite3-journal').write_bytes(b'interrupted')
    result = s.executor.run(c, envelope)
    assert result.status == 'blocked', result
    assert not s.world.calls


@pytest.mark.parametrize('failure', ['ip','container','network','unknown'])
def test_readiness_failures_never_activate_or_promote(integrated, signer, failure):
    s = integrated
    c, envelope = signed(s, signer)
    original = s.engine.inspect
    calls = []
    def inspect(kind, identity):
        raw = original(kind, identity)
        if s.world.present and kind == 'container':
            calls.append(1)
            if failure == 'container': raw['Id'] = '9'*64
            if failure == 'ip' and len(calls) % 2 == 0:
                raw['NetworkSettings']['Networks']['portfolio-project-router-private']['IPAddress'] = '172.31.0.3'
        if s.world.present and kind == 'network' and failure == 'network': raw['IPAM'] = {}
        return raw
    s.engine.inspect = inspect
    if failure == 'unknown': s.http_status = None
    result = s.executor.run(c, envelope)
    assert result.status in ('blocked', 'recovery-required'), result
    assert not s.world.route and ('gateway',) not in s.world.calls
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web') is None
    before = list(s.world.calls)
    assert s.executor.run(c, envelope).status == 'blocked'
    assert s.world.calls == before  # no unauthorized second mutation/replay


def test_local_source_proof_cannot_be_replaced_or_altered(integrated):
    s = integrated
    port = s.ports['runtime']
    result = port.collect('round')
    binding = SourceBinding('runtime', *s.engine.identity(), port.identity)
    attestor = LocalSourceAttestor(s.ports)
    assert attestor.verify(binding, port, result).result is result
    with pytest.raises(DockerError): attestor.verify(binding, port, replace(result))
    result.payload['scope']['trusted'] = False
    with pytest.raises(DockerError): attestor.verify(binding, port, result)


@pytest.mark.parametrize('raw', [b'', b'[]', b'{"a":1,"a":2}', b'x'*(1048576+1)])
def test_active_config_parser_closed(raw):
    with pytest.raises(DockerError): active_json(raw)


def test_distroless_channel_has_no_fallback():
    with pytest.raises(DockerError, match='unconfigured'):
        UnconfiguredActiveChannel().read_active('a'*64, timeout=1, limit=1048576)


@pytest.mark.parametrize('partial', [False, True])
def test_signed_bootstrap_then_separate_rollout_or_partial_fence(integrated, signer, partial):
    from scripts.docker_discovery import DockerObservation, discover
    from scripts.production_integration.bootstrap import BootstrapInput, BootstrapNetworkContract
    from scripts.production_integration.execution import BootstrapExecution
    from scripts.production_integration.runtime import CaddyRuntime
    from scripts.readiness import ReadinessEvidence
    from test_production_integration import paths
    s = integrated
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        for table in ('good', 'events', 'attempts', 'runs', 'authorizations'):
            journal.db.execute('DELETE FROM ' + table)  # synthetic initial host only
    inventory = s.engine.inventory
    s.engine.inventory = lambda kind: ()
    empty_docker = discover(s.engine, ())
    assert empty_docker == DockerObservation((), (), True)
    s.host.policy = replace(s.policy, network_hash=hashed(network_facts(s.facts, empty_docker)),
                            resources_hash=hashed(asdict(empty_docker)))
    def collect_bootstrap(*, before_observation):
        before_observation()
        return BootstrapInput(*s.engine.identity(), 0, True, discover(s.engine, ()), (), 'absent', 'absent', ())
    s.preflight.bootstrap_reader = SimpleNamespace(collect=collect_bootstrap)
    s.preflight.bootstrap_attestor = SimpleNamespace(verify=lambda sample, identity:sample)
    s.preflight.proxy = BootstrapNetworkContract('172.30.0.0/24', '172.30.0.1')
    s.preflight.private_subnet = '172.31.0.0/24'
    s.preflight.caddy = CaddyRuntime('caddy@sha256:'+'f'*64, '2.11.7', paths())
    actions, postconditions = [], []
    class Resources:
        def preflight(self, prepared, grant): grant.require()
        def apply(self, action, prepared, grant):
            grant.require()
            actions.append(action)
            if partial: raise DockerError('controlled-partial-bootstrap', True)
            if action == prepared.bootstrap.operations[-1]: s.engine.inventory = inventory
        def verify(self, prepared, grant):
            for _ in range(2):
                postconditions.append(discover(s.engine, ()))
                assert postconditions[-1].complete and len(postconditions[-1].resources) == 2
                assert s.ports['gateway'].port.read() == ()
            return ReadinessEvidence('verified', 'readiness-verified', 2, 2)
    s.executor.bootstrap = BootstrapExecution(Resources())
    with s.lock() as lease: c = s.preflight.prepare_locked('first-bootstrap', lease).candidate
    c, envelope = signed(s, signer, c, nonce='4'*32)
    result = s.executor.run(c, envelope)
    if partial:
        assert result.status == 'recovery-required'
        assert len(actions) == 1 and not postconditions
        assert s.executor.run(c, envelope).status == 'blocked'
        _, another = signed(s, signer, c, nonce='5'*32)
        assert s.executor.run(c, another).status == 'blocked'
        assert len(actions) == 1 and not s.world.calls
        return
    assert result.status == 'succeeded', result
    assert len(actions) == 5 and len(postconditions) == 2
    assert postconditions[0] == postconditions[1]
    s.host.policy = s.policy  # separately approved next-phase exact Engine policy
    rollout, rollout_envelope = signed(s, signer)
    assert s.executor.run(rollout, envelope).status == 'blocked'
    assert not s.world.calls
    assert s.executor.run(rollout, rollout_envelope).status == 'succeeded'
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.production_bootstrap(*s.engine.identity())
        assert journal.last_good('project-router', 'web')['run'] == '1'*32


@pytest.fixture
def fixed_composition(router, monkeypatch):
    from scripts.production_integration import composition
    from scripts.production_integration.docker import EngineContract
    from scripts.production_integration.installation import SealedInstallation
    from scripts.production_integration.runtime import CaddyRuntime
    from test_production_integration import state, paths
    monkeypatch.setattr(SealedInstallation, 'verify', lambda self:None)
    installation = SealedInstallation('a'*40, ())
    authority = SimpleNamespace()
    arguments = dict(engine_contract=EngineContract('unix:///run/docker.sock', 'daemon', 'a'*32, True),
        host_policy=None, prerequisites=authority, contracts=(router,), infrastructure=(),
        resolve_image=authority, proxy=proxy()[0], bootstrap_proxy=authority,
        private_subnet='172.31.0.0/24', private_network_id=NID,
        caddy=CaddyRuntime('caddy@sha256:'+'f'*64, '2.11.7', paths()), caddy_image=state(router)[2],
        active_channel=UnconfiguredActiveChannel(), gateway_runtime=authority, gateway_postconditions=authority,
        bootstrap_reader=authority, bootstrap_attestor=authority, bootstrap_resources=authority,
        producer_sha='b'*40, image=router.spec['components'][0]['image'], github=authority,
        registry=authority, crypto=authority, checks=(), publication_check=authority,
        signer_sha='b'*40, builder='unconfigured', slsa_builder='unconfigured')
    return installation, arguments


def test_fixed_composition_wires_existing_ports_and_stays_inert(fixed_composition):
    from scripts.production_integration import composition
    from scripts.production_integration.operator_approval import SSHSIGVerifier
    from scripts.production_integration.execution import ProductionExecutor
    installation, arguments = fixed_composition
    executor = composition.build(installation, **arguments)
    assert executor.preflight.image_registry is None and executor.image_preparation is None
    with pytest.raises(DockerError, match='production-image-registry-unavailable'):
        executor.preflight.prepare('prepare-project-router-image')
    assert type(executor) is ProductionExecutor and type(executor.verifier) is SSHSIGVerifier
    assert isinstance(executor.preflight.provider.reader, ProductionStateReader)
    assert isinstance(executor.preflight.contracts, PolicyHostContracts)
    assert executor.preflight.provider.reader.source_trust.attestor.ports['runtime'] is executor.preflight.provider.reader.ports['runtime']
    with pytest.raises(DockerError): composition.build(installation, **{**arguments, 'prerequisites': None})
    # Exact checked-in entry points still cannot build an executor/admin approval.
    from installation import bindings, admin_bindings
    with pytest.raises(DockerError): bindings.build(installation)
    with pytest.raises(RuntimeError): admin_bindings.authenticate_initial_installation('a'*64)


def test_active_double_read_detects_change(integrated):
    s = integrated
    port = s.ports['gateway'].port
    transport = port.transport
    reads = []
    def changed(*args, **kwargs):
        reads.append(1)
        return b'{"routes":[]}' if len(reads) == 1 else b'{"routes":["foreign"]}'
    transport.channel.read_active = changed
    with pytest.raises(DockerError, match='changed'): port.read()
    assert len(reads) == 2


def test_historical_contract_not_replaced_by_desired(integrated):
    s = integrated
    s.world.present = True
    spec = s.world.app.spec
    spec['components'][0]['runtimeSecurity']['user'] = '102:102'
    new = replace(s.world.app, spec_json=encode(spec))
    # Catalog contains the independently approved OLD contract. Actual runtime
    # remains old, even when desired has changed. No LKG supplies observed specs.
    from scripts.reconciliation import DesiredState
    s.world.desired = DesiredState(s.world.desired.git_revision, (new,))
    _, observed, _ = s.provider.refresh()
    assert observed.document['applications'][0]['components'][0]['runtime']['runtimeSecurity']['user'] == '101:101'
    s.inventory.contracts = (new,)
    with pytest.raises(DockerError, match='historical-contract'): s.provider.refresh()


@pytest.mark.parametrize('failure', ['unknown-ipam', 'changed-inventory', 'stale', 'bootstrap-identity'])
def test_unknown_or_changing_local_facts_block_before_mutation(integrated, signer, failure):
    s = integrated
    c, envelope = signed(s, signer)
    if failure == 'unknown-ipam':
        original = s.engine.inspect
        def inspect(kind, identity):
            raw = original(kind, identity)
            if kind == 'network': raw['IPAM'] = {}
            return raw
        s.engine.inspect = inspect
    elif failure == 'changed-inventory':
        original = s.engine.inventory
        calls = []
        def inventory(kind):
            result = original(kind)
            if kind == 'network':
                calls.append(1)
                result[0]['Options'] = {'changing': str(len(calls))}
            return result
        s.engine.inventory = inventory
    elif failure == 'stale': s.facts = replace(s.facts, captured_at=-10)
    else:
        # A real journal cannot attest the bootstrap to a changed host/daemon.
        s.engine.identity_value = ('a'*32, 'changed')
        s.host.policy = replace(s.policy, daemon='changed')
        s.facts = replace(s.facts, identity=s.engine.identity())
    result = s.executor.run(c, envelope)
    assert result.status == 'blocked'
    assert not s.world.calls


def test_missing_public_postconditions_never_promotes(integrated, signer):
    s = integrated
    c, envelope = signed(s, signer)
    s.ports['gateway'].postconditions = None
    result = s.executor.run(c, envelope)
    assert result.status == 'recovery-required'
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web') is None
    calls = list(s.world.calls)
    assert s.executor.run(c, envelope).status == 'blocked'
    assert s.world.calls == calls


@pytest.fixture
def composed_image(integrated, fixed_composition, monkeypatch):
    """Real fixed composition, preflight, release/SSHSIG and cache coordinator.

    Substitute only independent host/installation facts and transport I/O. The
    fixed GHCR factory and existing ImagePreparation implementation remain real.
    """
    from scripts.production_integration import composition, image_preparation
    from scripts.production_integration.release_transport import IMAGE_REPOSITORY
    from scripts.production_integration.image_preparation import ImagePreparation
    s = integrated
    _, arguments = fixed_composition
    installation = s.executor.verifier.installation
    monkeypatch.setattr(composition, 'installed_engine', lambda *a:s.engine)
    monkeypatch.setattr(composition, 'InstalledContentReader', lambda *a:s.preflight.content_reader)
    monkeypatch.setattr(composition, 'LocalHostSources', lambda *a:None)
    monkeypatch.setattr(composition, 'PolicyHostContracts', lambda *a, **kw:s.host)
    s.cache_calls, s.cache_present, s.cache_failure = [], False, None
    class RegistryTransport:
        def get(self, url, headers):
            s.cache_calls.append('registry')
            if url.startswith('https://ghcr.io/token?'):
                assert url == 'https://ghcr.io/token?service=ghcr.io&scope=repository:danilfaritovich/project-router:pull'
                assert headers == {}
                return b'{"token":"synthetic"}'
            prefix = 'https://ghcr.io/v2/danilfaritovich/project-router/'
            assert url.startswith(prefix) and headers['Authorization'] == 'Bearer synthetic'
            return s.blobs[url.rsplit('/', 1)[1]]
    monkeypatch.setattr(composition, 'HTTPSRead', RegistryTransport)
    def listing(args):
        assert args == image_preparation.LIST
        s.cache_calls.append('list')
        if s.cache_failure == 'incomplete': return b'incomplete'
        return encode({'Repository': IMAGE_REPOSITORY, 'Digest': s.preflight.image.split('@')[1],
                       'ID': s.world.image.image_id}).encode() if s.cache_present else b''
    s.engine._run = listing
    original_inspect = s.engine.inspect
    def inspect(kind, identity):
        if kind != 'image': return original_inspect(kind, identity)
        assert kind == 'image' and identity == s.preflight.image and s.cache_present
        s.cache_calls.append('inspect')
        raw = original_inspect(kind, identity)
        if s.cache_failure == 'digest': raw['RepoDigests'] = [IMAGE_REPOSITORY + '@sha256:' + 'f'*64]
        if s.cache_failure == 'platform': raw['Architecture'] = 'arm64'
        return raw
    s.engine.inspect = inspect
    def pull(argv, **kw):
        assert argv == ['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'image', 'pull',
                        '--platform', 'linux/amd64', s.preflight.image]
        assert kw['timeout'] == 120
        s.cache_calls.append('pull')
        if s.cache_failure == 'ambiguous': raise DockerError('docker-timeout', True)
        s.cache_present = True
    monkeypatch.setattr(image_preparation, 'run_bounded', pull)
    arguments.update(s.release_arguments, image=s.preflight.image,
                     image_registry=composition.project_router_image_registry)
    s.executor = composition.build(installation, **arguments)
    assert type(s.executor.image_preparation) is ImagePreparation
    s.executor.directory, s.executor.clock = s.directory, lambda:100
    s.preflight = s.executor.preflight
    # Freshness clock is controlled, host attestor uses the same clock.
    s.preflight.clock = lambda:0
    def cache_candidate():
        with s.lock() as lease:
            return s.preflight.prepare_locked('prepare-project-router-image', lease).candidate
    s.cache_candidate = cache_candidate
    return s


@pytest.mark.parametrize('already_present', [False, True])
def test_fixed_composition_cache_only_signed_scope_and_replay(composed_image, signer, already_present):
    s = composed_image
    s.cache_present = already_present
    def forbidden(*a, **kw): pytest.fail('cache scope attempted bootstrap/rollout')
    s.executor.bootstrap.execute = s.executor.adapter_factory = forbidden
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        bootstrap_good = journal.last_good('manager-bootstrap', 'gateway')
    c, envelope = signed(s, signer, s.cache_candidate())
    assert s.executor.run(c, envelope).status == 'succeeded'
    assert s.cache_calls.count('pull') == (0 if already_present else 1)
    assert s.cache_calls.count('inspect') >= 2
    assert not s.world.calls and not s.world.present
    assert s.executor.run(c, envelope).status == 'blocked'
    # A newly signed candidate is also spent; registry reads do not authorize pull.
    _, another = signed(s, signer, c, nonce='2'*32)
    assert s.executor.run(c, another).status == 'blocked'
    assert s.cache_calls.count('pull') == (0 if already_present else 1)
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert not journal.authorization_recovery()
        assert journal.last_good('project-router', 'web') is None
        assert journal.last_good('manager-bootstrap', 'gateway') == bootstrap_good
        assert journal.db.execute('SELECT state FROM authorizations WHERE nonce=?', ('1'*32,)).fetchone()[0] == 'succeeded'


@pytest.mark.parametrize('missing', ['registry', 'cache'])
def test_composed_cache_missing_dependency_blocks(composed_image, signer, missing):
    s = composed_image
    c, envelope = signed(s, signer, s.cache_candidate())
    if missing == 'registry':
        s.preflight.image_registry = None
        with pytest.raises(DockerError, match='production-image-registry-unavailable'): s.cache_candidate()
    else: s.executor.image_preparation = None
    assert s.executor.run(c, envelope).status == 'blocked'
    assert 'pull' not in s.cache_calls and not s.world.calls


@pytest.mark.parametrize('failure', ['digest', 'platform', 'incomplete', 'ambiguous'])
def test_composed_cache_unknown_outcome_fences_all_scopes(composed_image, signer, failure):
    s = composed_image
    c, envelope = signed(s, signer, s.cache_candidate())
    rollout, rollout_envelope = signed(s, signer, replace(c, scope='first-project-router-rollout'), nonce='3'*32)
    s.cache_failure = failure
    assert s.executor.run(c, envelope).status == 'recovery-required'
    pulls = s.cache_calls.count('pull')
    assert s.executor.run(c, envelope).status == 'blocked'
    _, another = signed(s, signer, c, nonce='2'*32)
    assert s.executor.run(c, another).status == 'blocked'
    assert s.executor.run(rollout, rollout_envelope).status == 'blocked'
    with pytest.raises(DockerError, match='production-host-recovery-required'): s.prepare()
    assert s.cache_calls.count('pull') == pulls and not s.world.calls
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.authorization_recovery()
        assert journal.last_good('project-router', 'web') is None


@pytest.mark.parametrize('failure', ['unapproved', 'caddy', 'digest', 'platform', 'unsigned', 'candidate'])
def test_composed_cache_untrusted_image_never_pulls(composed_image, signer, failure):
    s = composed_image
    c, envelope = signed(s, signer, s.cache_candidate())
    if failure == 'unapproved': s.release_arguments['crypto'].verify = lambda *a: ()
    elif failure == 'caddy': s.preflight.image = 'ghcr.io/danilfaritovich/portfolio-caddy@sha256:' + 'f'*64
    elif failure == 'digest': s.blobs[c.image.split('@')[1]] += b' '
    elif failure == 'platform':
        from scripts.production_integration.artifacts import blob_digest
        manifest = json.loads(s.blobs[c.image.split('@')[1]])
        config = json.loads(s.blobs[manifest['config']['digest']])
        config['architecture'] = 'arm64'
        raw = encode(config).encode()
        manifest['config'].update(digest=blob_digest(raw), size=len(raw))
        manifest_raw = encode(manifest).encode()
        s.blobs.update({blob_digest(raw):raw, blob_digest(manifest_raw):manifest_raw})
        s.preflight.image = c.image.split('@')[0] + '@' + blob_digest(manifest_raw)
        with pytest.raises(DockerError): s.cache_candidate()
    elif failure == 'unsigned': envelope = b'{}'
    else: s.preflight.producer_sha = 'f'*40
    assert s.executor.run(c, envelope).status == 'blocked'
    assert 'pull' not in s.cache_calls and not s.world.calls


def test_installed_registry_factory_is_fixed_fresh_and_bounded():
    from scripts.production_integration.composition import project_router_image_registry
    from scripts.production_integration.release_transport import GHCRRead, HTTPSRead, IMAGE_REPOSITORY
    first, second = project_router_image_registry(), project_router_image_registry()
    assert type(first) is GHCRRead and type(first.transport) is HTTPSRead
    assert first.repository == IMAGE_REPOSITORY and first._registry_auth is None
    assert first.transport is not second.transport and first.transport.remaining == 64
    with pytest.raises(DockerError, match='production-release-image-rejected'):
        first.collect('ghcr.io/danilfaritovich/portfolio-caddy@sha256:' + 'f'*64)
