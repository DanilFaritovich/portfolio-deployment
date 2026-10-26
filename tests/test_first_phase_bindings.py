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
from scripts.docker_discovery import DockerObservation, DockerResource, discover
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
    def collect(*, before_observation=None):
        if before_observation is not None:
            before_observation()
        return state.host
    local = SimpleNamespace(collect=collect)
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


def test_empty_host_with_engine_default_networks_and_null_ipam(reader):
    """Production-shaped Engine inspect: host/none IPAM.Config is JSON null."""
    port, state, _ = reader
    raw = [dict(Name=name, Id=str(i)*64, Driver=driver, Scope='local',
                IPAM={'Driver': 'default', 'Options': None, 'Config': configs})
           for i, (name, driver, configs) in enumerate([
               ('bridge', 'bridge', [{'Subnet': '172.17.0.0/16', 'Gateway': '172.17.0.1'}]),
               ('host', 'host', None), ('none', 'null', None)], 1)]
    state.docker = discover(SimpleNamespace(inventory=lambda kind: raw if kind == 'network' else []), ())
    assert state.docker.complete
    assert state.docker.resources[1].facts['ipam']['Config'] is None
    port.policy = replace(port.policy, network_hash=hashed(network_facts(state.host, state.docker)),
                          resources_hash=hashed(asdict(state.docker)))
    sample = port.collect()
    proposal = plan_bootstrap(sample, expected_identity=(sample.host, sample.daemon),
        proxy=BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1'),
        private_subnet='172.30.241.0/24', now=1, attestor=port)
    assert proposal.blockers == ('stage-09-4b-bootstrap-approval-and-execution-required',)
    assert proposal.operations
    # Even correctly classified builtins do not bypass the independently reviewed universe.
    port.policy = replace(port.policy, resources_hash=hashed({}))
    with pytest.raises(DockerError): port.verify(sample, (sample.host, sample.daemon))


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


@pytest.mark.parametrize('notification', ['missing', 'repeated'])
def test_composite_window_must_start_exactly_once(reader, notification):
    port, state, _ = reader
    def collect(*, before_observation):
        if notification == 'repeated':
            before_observation()
            before_observation()
        return state.host
    port.local.collect = collect
    with pytest.raises(DockerError, match='observation-window-unbound'):
        port.collect()
    assert port.last is None


@pytest.fixture
def clocked_reader(reader, monkeypatch):
    from scripts.production_integration import local_sources
    port, state, _ = reader
    state.verifications, state.reads = [], []
    state.verify_seconds, state.command_seconds, state.discovery_seconds = 8, 0.125, 0.5
    state.fail_verification = None
    def verify():
        state.verifications.append(state.time)
        if len(state.verifications) == state.fail_verification:
            raise DockerError('synthetic-seal-content-changed')
        state.time += state.verify_seconds
    port.installation.verify = verify
    port.installation.files = (('/usr/bin/ip', 'a'*64), ('/usr/bin/ss', 'b'*64))
    def approve(policy, identity):
        assert policy == port.policy and identity == state.host.identity
        verify()
        return policy
    port.prerequisites.verify = approve
    def run(argv, **kwargs):
        assert tuple(argv) in local_sources.COMMANDS.values()
        state.reads.append(state.time)
        state.time += state.command_seconds
        return b'' if argv[0] == '/usr/bin/ss' else b'[]'
    monkeypatch.setattr(local_sources, 'run_bounded', run)
    monkeypatch.setattr(local_sources, 'PATHS', ())
    port.local = local_sources.LocalHostSources(port.installation, port.engine, clock=lambda: state.time)
    network = {name: [] for name in ('routes4', 'routes6', 'rules4', 'rules6', 'listeners')}
    state.host = replace(state.host, network=network)
    port.policy = replace(port.policy, network_hash=hashed(network_facts(state.host, state.docker)))
    def discovery(*args):
        state.time += state.discovery_seconds
        return state.docker
    monkeypatch.setattr(phase, 'discover', discovery)
    return port, state


def test_slow_immutable_checks_precede_real_composite_observation_window(clocked_reader):
    port, state = clocked_reader
    sample = port.collect()
    assert len(state.verifications) == 3
    assert state.reads[0] == 25  # Every eight-second integrity check still ran.
    assert sample.captured_at == 26.625
    assert port.verify(sample, (sample.host, sample.daemon)) is sample


@pytest.fixture
def clocked_preflight(clocked_reader, monkeypatch):
    from scripts.production_integration import host_policy
    from scripts.production_integration.candidate import CandidatePreflight
    port, state = clocked_reader
    monkeypatch.setattr(host_policy, 'PATHS', ())
    monkeypatch.setattr(host_policy, 'discover', phase.discover)
    host = host_policy.PolicyHostContracts(port.installation, port.engine, port.local,
        SimpleNamespace(read=lambda lease: {'database': 'absent', 'recovery': 'unestablished'}),
        port.policy, prerequisites=port.prerequisites, contents=lambda: state.contents, clock=lambda: state.time)
    def content():
        port.installation.verify()
        return SimpleNamespace(git_revision='b'*40), b'manifest', b'policy'
    preflight = CandidatePreflight(manager_sha='b'*40, producer_sha='c'*40,
        image='ghcr.io/danilfaritovich/project-router@sha256:'+'d'*64,
        content_reader=content, engine=port.engine, contracts=host, attestor=host,
        bootstrap_reader=port, bootstrap_attestor=port,
        proxy=BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1'),
        private_subnet='172.30.241.0/24', caddy=SimpleNamespace(render=lambda: SimpleNamespace(model={'caddy': 'test'})),
        clock=lambda: state.time)
    return preflight, port, state


def test_full_bootstrap_preflight_excludes_all_slow_seals(clocked_preflight):
    preflight, port, state = clocked_preflight
    prepared = preflight.prepare_locked('first-bootstrap', object())
    assert prepared.candidate.scope == 'first-bootstrap'
    assert len(state.verifications) == 7
    assert state.reads[0] == 57
    assert state.time - state.reads[0] == 3.25
    assert port.last is not None


@pytest.mark.parametrize('discovery_seconds, accepted', [(0.9375, True), (0.95, False)])
def test_full_bootstrap_sources_share_one_five_second_window(clocked_preflight, discovery_seconds, accepted):
    preflight, _, state = clocked_preflight
    state.discovery_seconds = discovery_seconds
    if accepted:
        assert preflight.prepare_locked('first-bootstrap', object()).candidate.scope == 'first-bootstrap'
        assert state.time - state.reads[0] == 5
    else:
        with pytest.raises(DockerError, match='host-contract-unverified'):
            preflight.prepare_locked('first-bootstrap', object())


@pytest.mark.parametrize('failed_check', range(1, 8))
def test_full_bootstrap_seal_failure_precedes_all_mutable_reads(clocked_preflight, failed_check):
    preflight, _, state = clocked_preflight
    state.fail_verification = failed_check
    with pytest.raises(DockerError, match='synthetic-seal-content-changed'):
        preflight.prepare_locked('first-bootstrap', object())
    assert not state.reads


def test_full_bootstrap_rechecks_revoked_authority_after_source_seal(clocked_preflight):
    preflight, port, state = clocked_preflight
    original = port.prerequisites.verify
    def approve(policy, identity):
        if len(state.verifications) == 6:
            raise DockerError('synthetic-review-revoked')
        return original(policy, identity)
    port.prerequisites.verify = approve
    with pytest.raises(DockerError, match='synthetic-review-revoked'):
        preflight.prepare_locked('first-bootstrap', object())
    assert not state.reads


@pytest.mark.parametrize('notification', ['missing', 'repeated'])
def test_host_contract_window_must_begin_exactly_once(clocked_preflight, notification):
    preflight, _, state = clocked_preflight
    def collect(*, before_observation):
        if notification == 'repeated':
            before_observation()
            before_observation()
        return state.host
    preflight.contracts.local = SimpleNamespace(collect=collect)
    with pytest.raises(DockerError, match='observation-window-unbound'):
        preflight.prepare_locked('first-bootstrap', object())
    assert preflight.contracts.last is None


@pytest.mark.parametrize('cached', [False, True])
def test_signed_bootstrap_prepares_only_exact_caddy_digest(reader, monkeypatch, cached):
    from scripts import docker_adapter
    from scripts.production_integration import image_preparation
    port, _, _ = reader
    reference = 'ghcr.io/danilfaritovich/portfolio-caddy@sha256:'+'c'*64
    runtime = SimpleNamespace(image=reference)
    candidate = SimpleNamespace(host='a'*32, daemon='daemon')
    prepared = SimpleNamespace(candidate=candidate)
    grant = SimpleNamespace(candidate=candidate, require=lambda: None)
    engine = SimpleNamespace(identity=port.engine.identity, inspect=lambda kind, ref: {'reference': ref})
    resources = phase.BootstrapResources(port.installation, engine, port, None, None, None, None)
    resources.prepared, resources.runtime = prepared, runtime
    resources.evidence = SimpleNamespace(reference=reference)
    pulls, bindings = [], []
    monkeypatch.setattr(image_preparation, 'cached_image', lambda *args: cached)
    monkeypatch.setattr(docker_adapter, 'run_bounded', lambda argv, **kw: pulls.append(argv))
    monkeypatch.setattr(phase, 'bind_caddy_engine', lambda runtime, evidence, raw: bindings.append(raw))
    resources.prepare_image(prepared, runtime, grant)
    assert len(pulls) == (0 if cached else 1)
    if pulls:
        assert pulls[0] == ['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'image', 'pull',
                            '--platform', 'linux/amd64', reference]
    assert bindings == [{'reference': reference}]*2


@pytest.mark.parametrize('failure', ['no-evidence', 'grant', 'host', 'inventory', 'pull'])
def test_caddy_cache_failure_never_reaches_container_creation(reader, monkeypatch, failure):
    from scripts import docker_adapter
    from scripts.production_integration import image_preparation
    port, _, _ = reader
    runtime = SimpleNamespace(image='ghcr.io/danilfaritovich/portfolio-caddy@sha256:'+'c'*64)
    candidate = SimpleNamespace(host='a'*32, daemon='daemon')
    prepared = SimpleNamespace(candidate=candidate)
    grant = SimpleNamespace(candidate=candidate, require=lambda: None)
    resources = phase.BootstrapResources(port.installation, port.engine, port, None, None, None, None)
    resources.prepared, resources.runtime = prepared, runtime
    resources.evidence = SimpleNamespace(reference=runtime.image)
    def reject(*args, **kwargs): raise DockerError('synthetic-cache-failure')
    monkeypatch.setattr(image_preparation, 'cached_image', lambda *args: False)
    calls = []
    monkeypatch.setattr(docker_adapter, 'run_bounded', lambda *a, **kw: calls.append(a))
    if failure == 'no-evidence': resources.evidence = None
    elif failure == 'grant': grant.require = reject
    elif failure == 'host': port.engine.identity = lambda: ('b'*32, 'foreign')
    elif failure == 'inventory': monkeypatch.setattr(image_preparation, 'cached_image', reject)
    else: monkeypatch.setattr(docker_adapter, 'run_bounded', reject)
    with pytest.raises(DockerError): resources.prepare_image(prepared, runtime, grant)
    assert not calls


@pytest.mark.parametrize('failed_check', [1, 2, 3])
def test_each_integrity_failure_stops_before_mutable_reads(clocked_reader, failed_check):
    port, state = clocked_reader
    state.fail_verification = failed_check
    with pytest.raises(DockerError, match='synthetic-seal-content-changed'):
        port.collect()
    assert not state.reads and port.last is None


def test_authority_revocation_during_source_seal_check_blocks_observation(clocked_reader):
    port, state = clocked_reader
    state.revoked = False
    original_verify = port.installation.verify
    def verify():
        original_verify()
        if len(state.verifications) == 2:
            state.revoked = True
    def approve(policy, identity):
        if state.revoked:
            raise DockerError('synthetic-review-revoked')
        return policy
    port.installation.verify = verify
    port.prerequisites.verify = approve
    with pytest.raises(DockerError, match='synthetic-review-revoked'):
        port.collect()
    assert len(state.verifications) == 2 and not state.reads and port.last is None


@pytest.mark.parametrize('discovery_seconds, accepted', [(2.1875, True), (2.25, False)])
def test_composite_mutable_reads_keep_exact_five_second_limit(clocked_reader, discovery_seconds, accepted):
    port, state = clocked_reader
    state.discovery_seconds = discovery_seconds
    if accepted:
        assert port.collect().captured_at == 30
    else:
        with pytest.raises(DockerError, match='facts-changed-or-expired'):
            port.collect()
        assert port.last is None


def test_inner_kernel_observation_still_has_its_own_deadline(clocked_reader):
    port, state = clocked_reader
    state.command_seconds = 1.1
    with pytest.raises(DockerError, match='production-host-source-changed-or-expired'):
        port.collect()
    assert port.last is None


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
