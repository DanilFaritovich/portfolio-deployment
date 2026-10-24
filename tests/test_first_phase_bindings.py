"""First-phase ports with synthetic facts; live transports always forbidden."""
from dataclasses import asdict, replace
import hashlib
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.docker_adapter import DockerError
from scripts.docker_discovery import DockerObservation, DockerResource
from scripts.production_integration import first_phase as phase
from scripts.production_integration.bootstrap import BootstrapNetworkContract, plan_bootstrap
from scripts.production_integration.candidate import hashed
from scripts.production_integration.host_policy import HostPolicy, network_facts
from scripts.production_integration.local_sources import LocalHostFacts
from scripts.reconciliation import encode


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def forbidden(*a, **kw): pytest.fail('live IO attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def reader(tmp_path, monkeypatch):
    for name in ('caddy-config', 'caddy-lkg', 'certificates', 'caddy-data', 'caddy-admin'):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(phase, 'Path', lambda *p: tmp_path / p[-1])
    identity = ('a'*32, 'daemon')
    engine = SimpleNamespace(identity=lambda: identity)
    installation = SimpleNamespace(verify=lambda: None)
    host = LocalHostFacts(identity, 1, {'listeners': [], 'routes4': []}, ())
    docker = DockerObservation((), (), True)
    state = SimpleNamespace(host=host, docker=docker, contents=(), time=1)
    local = SimpleNamespace(collect=lambda: state.host)
    monkeypatch.setattr(phase, 'discover', lambda *a: state.docker)
    policy = HostPolicy(*identity, hashed(network_facts(host, docker)), hashed(asdict(docker)), hashed(()),
                        1024**3, 100, 'backup', 'console', 'ipam', 'budget', 'containment')
    # Synthetic authority only; not installed or serialized into production.
    authority = SimpleNamespace(verify=lambda p, i: p)
    port = phase.EmptyHostReader(installation, engine, local, policy, authority,
                                contents=lambda: state.contents, clock=lambda: state.time)
    return port, state, tmp_path


def test_empty_host_sample_is_bound_fresh_and_needs_no_runtime_ids(reader):
    port, _, _ = reader
    sample = port.collect()
    assert port.verify(sample, (sample.host, sample.daemon)) is sample
    proposal = plan_bootstrap(sample, expected_identity=(sample.host, sample.daemon),
        proxy=BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1'),
        private_subnet='172.30.241.0/24', now=1, attestor=port)
    assert proposal.blockers == ('stage-09-4b-bootstrap-approval-and-execution-required',)
    assert 'create-manager-proxy-with-reviewed-ipam' in proposal.operations
    with pytest.raises(DockerError): port.verify(replace(sample), (sample.host, sample.daemon))


@pytest.mark.parametrize('case', ['machine', 'stale', 'unsigned', 'policy', 'storage', 'incomplete', 'container'])
def test_empty_host_rejects_unknown_or_unapproved_facts(reader, case):
    port, state, path = reader
    if case == 'machine': port.engine.identity = lambda: ('b'*32, 'daemon')
    elif case == 'stale': state.host = replace(state.host, captured_at=-10)
    elif case == 'unsigned': port.prerequisites = SimpleNamespace(verify=lambda *a: None)
    elif case == 'policy': port.policy = replace(port.policy, network_hash='sha256:'+'0'*64)
    elif case == 'storage': (path / 'caddy-data' / 'foreign').write_bytes(b'x')
    elif case == 'incomplete': state.docker = replace(state.docker, complete=False)
    else:
        state.docker = replace(state.docker, resources=(DockerResource('container', 'c'*64, 'foreign', '{}'),))
    with pytest.raises(DockerError): port.collect()
    assert port.last is None


def test_sample_expires_and_policy_cannot_be_changed_after_collection(reader):
    port, state, _ = reader
    sample = port.collect()
    state.time = 7
    with pytest.raises(DockerError): port.verify(sample, (sample.host, sample.daemon))
    state.time = 1
    port.policy = replace(port.policy, containment='different')
    with pytest.raises(DockerError): port.verify(sample, (sample.host, sample.daemon))


def test_resource_preflight_requires_real_caddy_collector(reader):
    port, _, _ = reader
    resources = phase.BootstrapResources(port.installation, port.engine, port, None, None,
                                        SimpleNamespace(collect=lambda *a: True), 'a'*40)
    with pytest.raises(DockerError, match='caddy-authority-required'):
        resources.preflight(None, None)
    with pytest.raises(DockerError, match='preflight-required'):
        resources.verify_created(None, None)


def test_created_resource_absence_does_not_become_readiness(reader, monkeypatch, tmp_path):
    port, _, _ = reader
    port.local.collect = lambda: LocalHostFacts(('a'*32, 'daemon'), 1, {}, ())
    monkeypatch.setattr(phase, 'storage_contents', lambda: ())
    (tmp_path / 'fixed').mkdir()
    (tmp_path / 'fixed' / 'Caddyfile').write_text(phase.content(()))
    runtime = SimpleNamespace(paths=SimpleNamespace(gateway_config='fixed'))
    resources = phase.BootstrapResources(port.installation, port.engine, port, None, None, None, None)
    prepared = SimpleNamespace(candidate=SimpleNamespace(host='a'*32, daemon='daemon'))
    resources.prepared, resources.evidence, resources.runtime = prepared, object(), runtime
    with pytest.raises(DockerError, match='created-resources-missing'):
        resources.verify_created(prepared, runtime)


@pytest.mark.parametrize('changed', [False, True])
def test_two_actual_postconditions_required_before_readiness(reader, monkeypatch, changed):
    port, _, _ = reader
    resources = phase.BootstrapResources(port.installation, port.engine, port, None, None, None, None, clock=lambda: 1)
    prepared, runtime = object(), object()
    resources.prepared, resources.evidence, resources.runtime = prepared, object(), runtime
    calls = []
    def check(*a):
        calls.append(1)
        return len(calls) if changed else ('observed-identities',)
    monkeypatch.setattr(resources, '_check_created', check)
    if changed:
        with pytest.raises(DockerError, match='postconditions-changed'): resources.verify_created(prepared, runtime)
    else:
        proof = resources.verify_created(prepared, runtime)
        assert (proof.status, proof.probes, proof.consecutive) == ('verified', 2, 2)
    assert len(calls) == 2


@pytest.fixture
def admin(monkeypatch):
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    helper = load('operator_bootstrap', 'installation/operator_bootstrap.py')
    binding = load('c46_admin_binding', 'installation/admin_bindings.py')
    files = tuple(sorted((('/usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py', 'b'*64),
                          ('/usr/local/lib/portfolio-deployment-bootstrap/admin_bindings.py', 'c'*64))))
    digest = hashlib.sha256(helper.canonical(list(map(list, files)))).hexdigest()
    decision = binding.InitialInstallationDecision('a'*32, 'a'*40, 'a'*64, 'b'*64, 'c'*64,
                                                  digest, files, 'd'*32, 100, 200)
    return binding, decision


def test_admin_exact_tuple_one_use_authority_and_closed_default(admin):
    binding, decision = admin
    calls = []
    def consume(d):
        if calls: return None
        calls.append(d)
        return d
    authority = SimpleNamespace(consume=consume)
    approval = binding.authenticate_decision('a'*64, decision, authority=authority, now=150)
    assert approval.host_id == decision.machine_id and approval.external_files == decision.external_files
    with pytest.raises(RuntimeError): binding.authenticate_decision('a'*64, decision, authority=authority, now=150)
    with pytest.raises(RuntimeError): binding.authenticate_initial_installation('a'*64)


@pytest.mark.parametrize('change', [dict(machine_id='host'), dict(manager_sha='main'), dict(helper_sha256='e'*64),
    dict(binding_sha256='e'*64), dict(external_inventory_sha256='e'*64), dict(expires=101), dict(decision_id=''),
    dict(external_files=()), dict(bundle_sha256='e'*64)])
def test_admin_invalid_tuple_never_consumes_authority(admin, change):
    binding, decision = admin
    def forbidden(*a): pytest.fail('invalid tuple reached administrative authority')
    with pytest.raises(RuntimeError):
        binding.authenticate_decision('a'*64, replace(decision, **change), authority=SimpleNamespace(consume=forbidden), now=150)


def test_admin_unsigned_authority_cannot_issue_installation(admin):
    binding, decision = admin
    with pytest.raises(RuntimeError):
        binding.authenticate_decision('a'*64, decision, authority=SimpleNamespace(consume=lambda d: True), now=150)


@pytest.mark.parametrize('failure', [None, 'plan', 'runtime', 'acceptance', 'missing-acceptance'])
def test_resource_preflight_uses_existing_v2_source_oci_signature_verifier(reader, failure):
    from test_caddy_producer import consumer_fixture
    from scripts.production_integration.caddy_release import PREDICATE
    assert PREDICATE.endswith('/v2')
    port, _, _ = reader
    collector, runtime, sha, _, _, statements = consumer_fixture()
    evidence = collector.collect(runtime, sha)
    sample = port.collect()
    proxy = BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1')
    proposal = plan_bootstrap(sample, expected_identity=(sample.host, sample.daemon), proxy=proxy,
                              private_subnet='172.30.241.0/24', now=1, attestor=port)
    baseline = asdict(sample)
    baseline.pop('captured_at')
    contract = {'operations': proposal.operations, 'baseline': baseline, 'proxy': asdict(proxy),
                'privateSubnet': '172.30.241.0/24'}
    candidate = SimpleNamespace(host=sample.host, daemon=sample.daemon, scope='first-bootstrap',
        plan_hash=hashed(contract), runtime_hash=hashed(runtime.render().model))
    prepared = SimpleNamespace(candidate=candidate, bootstrap=proposal, resource_contract=encode(contract))
    import json
    port.engine.inspect = lambda kind, ref: {'Id': evidence.image_id, 'RepoDigests': [evidence.reference],
        'Os': 'linux', 'Architecture': 'amd64', 'Config': json.loads(evidence.defaults_json)}
    acceptance = SimpleNamespace(verify=lambda runtime, proof, identity: proof)
    resources = phase.BootstrapResources(port.installation, port.engine, port, proxy, '172.30.241.0/24',
                                        collector, sha, runtime_acceptance=acceptance)
    if failure == 'plan': candidate.plan_hash = 'sha256:'+'f'*64
    elif failure == 'runtime': candidate.runtime_hash = 'sha256:'+'f'*64
    elif failure == 'acceptance': acceptance.verify = lambda *a: True
    elif failure == 'missing-acceptance': resources.runtime_acceptance = None
    if failure:
        with pytest.raises(DockerError): resources.preflight(prepared, runtime)
        assert resources.prepared is None and resources.evidence is None
    else:
        assert resources.preflight(prepared, runtime) is prepared
        assert statements[1]['predicateType'] == PREDICATE
        assert resources.evidence == evidence


def test_fixed_first_phase_composition_has_no_rollout_ports(reader, monkeypatch):
    from scripts.production_integration.docker import EngineContract
    from scripts.production_integration.installation import SealedInstallation
    from scripts.production_integration.execution import ProductionExecutor
    from scripts.production_integration.mutations import ManagedBootstrapPort
    from scripts.production_integration.operator_approval import SSHSIGVerifier
    from test_caddy_producer import consumer_fixture
    port, _, _ = reader
    collector, runtime, sha, _, _, _ = consumer_fixture()
    runtime = replace(runtime, paths=replace(runtime.paths, state='/srv/portfolio/state', lock='/srv/portfolio/locks',
        journal='/srv/portfolio/journal', gateway_config='/srv/portfolio/caddy-config',
        certificate_data='/srv/portfolio/certificates', gateway_data='/srv/portfolio/caddy-data'))
    monkeypatch.setattr(SealedInstallation, 'verify', lambda self: None)
    installation = SealedInstallation('a'*40, ())
    args = dict(engine_contract=EngineContract('unix:///run/docker.sock', 'daemon', 'a'*32, True),
        host_policy=port.policy, prerequisites=port.prerequisites,
        proxy=BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1'), private_subnet='172.30.241.0/24',
        caddy=runtime, caddy_source_sha=sha, caddy_github=collector.github, caddy_registry=collector.registry,
        caddy_crypto=collector.crypto, caddy_checks=collector.checks, caddy_publication_check=collector.publication,
        caddy_acceptance=object(), producer_sha='b'*40, image='ghcr.io/danilfaritovich/project-router@sha256:'+'d'*64,
        github=object(), registry=object(), crypto=object(), checks=(), publication_check=object(),
        signer_sha='b'*40, builder='unconfigured', slsa_builder='unconfigured')
    executor = phase.build_first_phase(installation, **args)
    assert type(executor) is ProductionExecutor and type(executor.verifier) is SSHSIGVerifier
    assert type(executor.bootstrap.port) is ManagedBootstrapPort
    assert executor.preflight.provider is None
    assert executor.adapter_factory is executor.gateway is executor.probes is executor.image_preparation is None
    assert executor.preflight.bootstrap_reader is executor.preflight.bootstrap_attestor
    assert executor.preflight.engine.host_identity.__class__.__name__ == 'MachineIdentity'
    for field in ('prerequisites', 'caddy_acceptance', 'caddy_crypto'):
        with pytest.raises(DockerError): phase.build_first_phase(installation, **{**args, field: None})
    with pytest.raises(DockerError):
        phase.build_first_phase(installation, **{**args, 'engine_contract': replace(args['engine_contract'], host_id='b'*32)})
    from installation import bindings
    with pytest.raises(DockerError, match='bindings-unavailable'): bindings.build(installation)


@pytest.mark.parametrize('failure', [None, 'missing-network', 'private-ipam', 'private-owner', 'proxy-member',
                                   'foreign-volume', 'changed-active', 'foreign-config', 'capacity'])
def test_created_resources_validate_actual_ipam_and_two_active_rounds(reader, monkeypatch, failure):
    from scripts.compose import LABEL, labels
    from scripts.production_integration.gateway_read import GatewaySnapshot
    port, state, directory = reader
    cid, nid, pid = 'c'*64, 'd'*64, 'e'*64
    def network(name, identity, internal, subnet, owner, members):
        return DockerResource('network', identity, name, encode({'driver': 'bridge', 'scope': 'local',
            'internal': internal, 'options': {}, 'containers': members, 'attachable': False, 'ingress': False,
            'enableIPv6': False, 'labels': owner, 'ipam': {'Driver': 'default', 'Options': {},
                'Config': [{'Subnet': subnet, 'Gateway': subnet.rsplit('.', 1)[0]+'.1'}]}}))
    proxy = BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1')
    net = network('portfolio-proxy', nid, False, '172.30.240.0/24',
                  {LABEL+'.managed': 'true', LABEL+'.role': 'proxy'}, {cid: {}})
    priv = network('portfolio-project-router-private', pid, True, '172.30.241.0/24',
                   {**labels('project-router'), 'com.docker.compose.project': 'portfolio-project-router',
                    'com.docker.compose.network': 'private'}, {})
    container = DockerResource('container', cid, 'portfolio-gateway-caddy-1', encode({'labels': {
        'com.docker.compose.project': 'portfolio-gateway', 'com.docker.compose.service': 'caddy'}}))
    state.docker = DockerObservation((net, priv, container), (), True)
    if failure in ('private-ipam', 'private-owner'):
        facts = priv.facts
        if failure == 'private-ipam': facts['ipam']['Config'][0]['Gateway'] = '172.30.241.2'
        else: facts['labels'] = {}
        state.docker = replace(state.docker, resources=(net, replace(priv, facts_json=encode(facts)), container))
    elif failure == 'proxy-member':
        facts = net.facts
        facts['containers']['f'*64] = {}
        state.docker = replace(state.docker, resources=(replace(net, facts_json=encode(facts)), priv, container))
    elif failure == 'missing-network': state.docker = replace(state.docker, resources=(net, container))
    elif failure == 'foreign-volume':
        state.docker = replace(state.docker, resources=(*state.docker.resources, DockerResource('volume', 'foreign', 'foreign', '{}')))
    if failure == 'capacity': state.host = replace(state.host, storage=({'availableBytes': 0, 'availableInodes': 0},))
    (directory / 'fixed').mkdir()
    (directory / 'fixed' / 'Caddyfile').write_text('foreign' if failure == 'foreign-config' else phase.content(()))
    runtime = SimpleNamespace(paths=SimpleNamespace(gateway_config='fixed'), version='2.11.7')
    monkeypatch.setattr(phase, 'storage_contents', lambda: ())
    sample = GatewaySnapshot((), '', 'sha256:'+'f'*64, '2.11.7', True, True, True, cid, ('observed-UDS',))
    observed_ids, reads = [], []
    def pinned(*a, **kw):
        observed_ids.append(kw)
        return SimpleNamespace(inspect=lambda channel: sample)
    monkeypatch.setattr(phase, 'PinnedCaddyRuntime', pinned)
    def read(*a, **kw):
        reads.append(1)
        return b'{"changed":true}' if failure == 'changed-active' and len(reads) == 2 else b'{}'
    monkeypatch.setattr(phase, 'UnixActiveChannel', lambda p: SimpleNamespace(read_active=read))
    monkeypatch.setattr(phase, 'InstalledConfigAdapter', lambda i: SimpleNamespace(adapt=lambda r: b'{}'))
    prepared = SimpleNamespace(candidate=SimpleNamespace(host='a'*32, daemon='daemon'),
                               resource_contract=encode({'baseline': {'docker': {'resources': []}}}))
    resources = phase.BootstrapResources(port.installation, port.engine, port, proxy, '172.30.241.0/24', None, None)
    resources.prepared, resources.evidence, resources.runtime = prepared, SimpleNamespace(image_id=sample.image_config_id), runtime
    if failure:
        with pytest.raises(DockerError): resources.verify_created(prepared, runtime)
    else:
        proof = resources.verify_created(prepared, runtime)
        assert proof.status == 'verified' and proof.consecutive == 2
        assert len(reads) == 4
        assert observed_ids == [{'container_id': cid, 'network_id': nid}]*2
