"""Stage09.4A acceptance with fakes only; no production credentials/transports."""
from dataclasses import asdict, replace
import json
import os
import socket
import subprocess

import pytest

from scripts.compose_version import supported_compose
from scripts.deployment import host_lock, LockLease, owns_host_lock
from scripts.deployment_journal import Journal, JournalError
from scripts.docker_adapter import DockerError, LocalDockerAdapter, command_failure
from scripts.docker_discovery import DockerObservation
from scripts.production.probes import normalize
from scripts.production_integration.authorization import OperationCandidate, ApprovalClaims, review_approval
from scripts.production_integration.bootstrap import BootstrapInput, BootstrapNetworkContract, plan_bootstrap
from scripts.production_integration.caddy_image import COMMAND, inspect_caddy_contract
from scripts.production_integration.gateway import content
from scripts.production_integration.gateway_read import ManagedGatewayReadPort
from scripts.production_integration.http_read import InternalHTTPRead
from scripts.production_integration.locking import validate_lock_path, require_production_lease
from scripts.production_integration.release import ReleaseVerifier, ReleaseFacts, PRODUCER, MANAGER, ORIGIN
from scripts.production_integration.artifacts import SourceRequest
from scripts.production_integration.runtime import ProxyContract, CaddyRuntime
from scripts.production_integration.trust import UnconfiguredPrivilegedExecution
from scripts.reconciliation import encode
from test_deployment import resource
from test_production_integration import paths, reader_fixture, router, registration


@pytest.fixture(autouse=True)
def no_real_transports(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Stage09.4A test attempted real transport/process')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.mark.parametrize('version', ['5.6.0', 'v5.6.0', '5.6.1', 'v5.7.0', '6.0.0', 'v6.2.1', '2.35.0', 'v2.40.3', '2.40.3+ds1-0ubuntu1~24.04.1'])
def test_shared_compose_policy_and_preflight(version):
    assert supported_compose(version)
    assert normalize('compose', version.encode()) == {'value': version}
    adapter = object.__new__(LocalDockerAdapter)
    from test_compose_version import cli_reply
    adapter._run = lambda args: cli_reply(args) if '--help' in args else version.encode() if args[0] == 'compose' else b'{"Os":"linux","Arch":"amd64"}'
    adapter.prerequisites()


@pytest.mark.parametrize('version', ['1.29.2', '0.0.0', '6.0.0-rc1',
                                     '5.6.0-rc1', '2.40.0-rc1', 'v05.6.0', '5.6.0\nanything', '5.6', ''])
def test_invalid_compose_versions_rejected(version):
    assert not supported_compose(version)


@pytest.mark.parametrize('case', ['missing-policy', 'wrong-source', 'wrong-host', 'wrong-daemon', 'no-attestor', 'boolean-proof'])
def test_booleans_and_nonempty_identity_cannot_promote_state(router, monkeypatch, case):
    reader, ports, _ = reader_fixture(router, monkeypatch)
    if case == 'missing-policy': reader.source_trust = None
    elif case == 'wrong-source': ports[0].change = lambda r: replace(r, identity='nonempty-forged')
    elif case in ('wrong-host', 'wrong-daemon'):
        reader.docker.identity = lambda: ('b'*32, 'daemon') if case == 'wrong-host' else ('a'*32, 'foreign')
    elif case == 'no-attestor': reader.source_trust.attestor = None
    else: reader.source_trust.attestor.verify = lambda *args: True
    provider = reader.provider()
    with pytest.raises(DockerError): provider.refresh()
    assert provider.sample is None


def test_unconfigured_privilege_is_separate_and_never_sudo():
    for command in ('docker info', 'sudo -n docker info', 'sh', {'command': 'sudo docker up'}):
        with pytest.raises(DockerError, match='not-provisioned'):
            UnconfiguredPrivilegedExecution().invoke(command)
    error = command_failure(1, b'permission denied on Docker socket SECRET')
    assert error.code == 'docker-permission-denied' and not error.unknown
    assert 'SECRET' not in str(error)


@pytest.mark.parametrize('args', [['container', 'ls', '--format', '{{json .}}'],
                                  ['container', 'inspect', '--help'], ['image', 'inspect', '$(sudo x)'],
                                  ['compose', 'up'], ['sudo', '-n', 'docker', 'info']])
def test_production_transport_rejects_unbounded_commands_before_contact(args):
    from scripts.production_integration.docker import ProductionDockerAdapter
    adapter = ProductionDockerAdapter(None, None)
    adapter.identity = lambda: pytest.fail('invalid command contacted Engine')
    with pytest.raises(DockerError): adapter._run(args)


def test_bounded_status_transport_deadline_limit_and_redaction(monkeypatch):
    from scripts.production_integration import http_read as module
    class Sock:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setblocking(self, *args): pass
        def connect_ex(self, address): assert address == ('172.31.0.2', 8080); return 0
        def getsockopt(self, *args): return 0
        def send(self, request): return len(request)
        def recv(self, limit): return reply[:limit]
    class Selector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def register(self, *args): pass
        def modify(self, *args): pass
        def select(self, timeout): return [True] if ready else []
    monkeypatch.setattr(module.socket, 'socket', lambda *args: Sock())
    monkeypatch.setattr(module.selectors, 'DefaultSelector', Selector)
    port = InternalHTTPRead('172.31.0.0/24', [(8080, '/en/')])
    ready, reply = True, b'HTTP/1.1 200 OK\r\n'
    assert port.get('172.31.0.2', 8080, '/en/', timeout=1) == 200
    for ready, reply in [(False, b''), (True, b'x'*4096), (True, b'SECRET\r\n')]:
        with pytest.raises(DockerError, match='^production-http-read-unavailable$'):
            port.get('172.31.0.2', 8080, '/en/', timeout=1)


def bootstrap_sample():
    return BootstrapInput('a'*32, 'daemon', 10, True, DockerObservation((), (), True), (), 'absent', 'absent', ())


class BootstrapAttestor:
    def verify(self, sample, expected): return sample  # fake authenticated observation


def bootstrap(sample=None, **kwargs):
    args = dict(expected_identity=('a'*32, 'daemon'), proxy=BootstrapNetworkContract('172.30.0.0/24', '172.30.0.1'),
                private_subnet='172.31.0.0/24', now=10, attestor=BootstrapAttestor())
    args.update(kwargs)
    return plan_bootstrap(sample or bootstrap_sample(), **args)


def test_first_host_proposal_is_ordered_bound_and_always_blocked():
    plan = bootstrap()
    assert plan.operations[:2] == ('prepare-exact-caddy-image', 'create-manager-proxy-with-reviewed-ipam')
    assert plan.release_sequence.index('verify-application-readiness') < plan.release_sequence.index('activate-first-public-route')
    assert plan.release_sequence.index('officially-validate-complete-caddy-candidate') < plan.release_sequence.index('activate-first-public-route')
    assert not any('deploy-application' in op or 'activate-first-public-route' in op for op in plan.operations)
    assert plan.blockers and 'no-previous-release' in plan.rollback
    assert plan.identity != bootstrap(private_subnet='172.29.0.0/24').identity
    assert content(()) == '{\n    admin unix//run/portfolio-caddy-admin/admin.sock|0600\n}\n'
    assert 'reverse_proxy' not in content(())


@pytest.mark.parametrize('case', ['untrusted', 'stale', 'future', 'incomplete', 'foreign-daemon', 'ports', 'gateway-foreign',
                                  'gateway-unknown', 'storage-foreign', 'ipam', 'private-conflict', 'named-proxy', 'foreign-volume'])
def test_conflicting_host_has_no_bootstrap_operations(case):
    sample = bootstrap_sample()
    kwargs = {}
    if case == 'untrusted': kwargs['attestor'] = None
    elif case == 'stale': sample = replace(sample, captured_at=0)
    elif case == 'future': sample = replace(sample, captured_at=11)
    elif case == 'incomplete': sample = replace(sample, complete=False)
    elif case == 'foreign-daemon': sample = replace(sample, daemon='foreign')
    elif case == 'ports': sample = replace(sample, listeners=(80,))
    elif case.startswith('gateway-'): sample = replace(sample, gateway=case.split('-')[1])
    elif case == 'storage-foreign': sample = replace(sample, storage='foreign')
    else:
        name = {'named-proxy': 'portfolio-proxy', 'private-conflict': 'portfolio-project-router-private',
                'foreign-volume': 'portfolio-gateway-data'}.get(case, 'unrelated')
        r = resource('volume' if case == 'foreign-volume' else 'network', name, {},
                     ipam={'Config': [{'Subnet': '172.30.0.0/16'}]})
        sample = replace(sample, docker=replace(sample.docker, resources=(r,)))
    proposal = bootstrap(sample, **kwargs)
    assert not proposal.operations and proposal.blockers


def test_existing_proxy_requires_exact_corroborated_id():
    from test_production_integration import proxy
    contract, network = proxy()
    sample = replace(bootstrap_sample(), docker=DockerObservation((network,), (), True))
    policy = BootstrapNetworkContract(contract.subnet, contract.gateway, contract.network_id)
    proposal = bootstrap(sample, proxy=policy)
    assert proposal.operations and 'create-manager-proxy-with-reviewed-ipam' not in proposal.operations
    assert not bootstrap(sample, proxy=replace(policy, existing_network_id=None)).operations
    assert not bootstrap(sample, proxy=replace(policy, existing_network_id='f'*64)).operations
    assert not bootstrap(proxy=policy).operations  # approved existing identity disappeared


def test_storage_budgets_and_missing_paths_fail_closed(tmp_path):
    from scripts.production_integration.storage import StorageBudgets, validate_storage_directory
    budgets = StorageBudgets(*[10485760]*8)
    budgets.validate()
    for value in (True, 0, -1, 10737418241):
        with pytest.raises(DockerError): replace(budgets, journal=value).validate()
    with pytest.raises(DockerError): StorageBudgets(*[10737418240]*8).validate()
    for path in ('relative', str(tmp_path/'missing'), str(tmp_path/'../unsafe')):
        with pytest.raises(DockerError): validate_storage_directory(path, uid=os.geteuid())


def test_canonical_lock_metadata_rejects_unsafe_parents(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    import stat
    from scripts.production_integration.locking import CANONICAL_LOCK, CANONICAL_LOCK_DIRECTORY
    monkeypatch.setattr(os, 'geteuid', lambda: 1001)
    change = {}
    def metadata(path):
        path = str(path)
        values = dict(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_nlink=1)
        if path == CANONICAL_LOCK_DIRECTORY: values.update(st_mode=stat.S_IFDIR | 0o700, st_uid=1001)
        if path == CANONICAL_LOCK: values.update(st_mode=stat.S_IFREG | 0o600, st_uid=1001)
        if path == change.get('path'): values.update(change['values'])
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path, 'lstat', metadata)
    validate_lock_path()
    for path, values in [('/srv', {'st_mode':stat.S_IFLNK | 0o755}),
                         ('/srv/portfolio', {'st_mode':stat.S_IFDIR | 0o777}),
                         (CANONICAL_LOCK, {'st_uid':1002}), (CANONICAL_LOCK, {'st_nlink':2}),
                         (CANONICAL_LOCK, {'st_mode':stat.S_IFREG | 0o644})]:
        change.update(path=path, values=values)
        with pytest.raises(DockerError): validate_lock_path()


def candidate():
    return OperationCandidate('a'*32, 'daemon', 'b'*40, 'c'*40,
                             'ghcr.io/danilfaritovich/project-router@sha256:'+'d'*64,
                             *['sha256:'+'e'*64]*8, 'first-project-router-rollout')


class ApprovalVerifier:
    def __init__(self, claims): self.claims = claims
    def authenticate(self, envelope): return self.claims


def test_approval_is_bound_to_every_candidate_field_and_window():
    c = candidate()
    claims = ApprovalClaims(c.identity, 'operator', 10, 20, 'a'*32)
    assert review_approval(c, b'signed-envelope', verifier=ApprovalVerifier(claims), now=15) == claims
    for key in asdict(c):
        value = getattr(c, key)
        new = 'first-bootstrap' if key == 'scope' else value[:-1] + ('f' if value[-1] != 'f' else 'e')
        changed = replace(c, **{key: new})
        changed.validate()
        with pytest.raises((DockerError, ValueError)):
            review_approval(changed, b'envelope', verifier=ApprovalVerifier(claims), now=15)
    for t in (9, 20, float('nan')):
        with pytest.raises(DockerError): review_approval(c, b'envelope', verifier=ApprovalVerifier(claims), now=t)
    with pytest.raises(DockerError): review_approval(c, {'verified': True}, now=15)
    with pytest.raises(DockerError): review_approval(c, {}, verifier=ApprovalVerifier(True), now=15)


def release_fixture():
    c = candidate()
    request = SourceRequest(PRODUCER, c.producer_sha, c.manager_sha, c.image, ORIGIN)
    checks = ((PRODUCER, 'ci', 1), (MANAGER, 'offline', 1))
    facts = ReleaseFacts(((PRODUCER, c.producer_sha), (MANAGER, c.manager_sha)),
                         ((PRODUCER, c.producer_sha, 'ci', 1), (MANAGER, c.manager_sha, 'offline', 1)),
                         (PRODUCER, c.producer_sha, 'refs/heads/main', c.image),
                         (c.image, PRODUCER, c.producer_sha, 'reviewed-workflow@'+'f'*40), ORIGIN,
                         c.manifest_hash, c.policy_hash, c.identity)
    class Collector:
        def collect_authenticated(self, *args): return facts
    verifier = ReleaseVerifier(c, required_checks=checks, allowed_builders=(facts.provenance[3],), collector=Collector())
    return request, facts, verifier


@pytest.mark.parametrize('field', list(ReleaseFacts.__dataclass_fields__))
def test_missing_or_mismatched_release_evidence_blocks(field):
    request, facts, verifier = release_fixture()
    assert not verifier.verify(request).blockers
    wrong = () if isinstance(getattr(facts, field), tuple) else 'unverified'
    changed = replace(facts, **{field: wrong})
    verifier.collector.collect_authenticated = lambda *args: changed
    with pytest.raises(DockerError): verifier.verify(request)


def test_release_external_verifier_missing_and_wrong_origin_block():
    request, _, verifier = release_fixture()
    verifier.collector = None
    with pytest.raises(DockerError, match='unconfigured'): verifier.verify(request)
    with pytest.raises(DockerError): verifier.verify(replace(request, build_origin='https://foreign.test'))


def test_canonical_lock_policy_and_real_flock_preserve_inode(tmp_path):
    with pytest.raises(DockerError, match='path-mismatch'): validate_lock_path(str(tmp_path/'host.lock'))
    with pytest.raises(DockerError): require_production_lease(LockLease())
    directory = tmp_path/'lock'
    with host_lock(directory) as lease:
        assert owns_host_lock(lease) and not owns_host_lock(LockLease())
        inode = os.stat(lease.path).st_ino
        with pytest.raises(DockerError, match='busy'):
            with host_lock(directory): pass
        with Journal(tmp_path/'journal', lease) as journal:
            assert journal.incomplete() == []
    assert not owns_host_lock(lease) and os.stat(directory/'host.lock').st_ino == inode
    with pytest.raises(JournalError):
        with Journal(tmp_path/'journal', LockLease()): pass


@pytest.mark.parametrize('case', ['symlink-dir', 'symlink-file', 'mode'])
def test_lock_unsafe_metadata_rejected(tmp_path, case):
    directory = tmp_path/'locks'
    directory.mkdir(mode=0o700)
    if case == 'mode': directory.chmod(0o777)
    elif case == 'symlink-file': (directory/'host.lock').symlink_to(tmp_path/'foreign')
    else:
        alias = tmp_path/'alias'; alias.symlink_to(directory); directory = alias
    with pytest.raises(DockerError):
        with host_lock(directory): pass


def test_concrete_http_read_is_restricted_and_redacts_errors():
    calls = []
    def exchange(*args):
        calls.append(args)
        return 302
    port = InternalHTTPRead('172.31.0.0/24', [(8080, '/en/')], exchange=exchange)
    assert port.get('172.31.0.2', 8080, '/en/', timeout=2) == 302
    assert calls == [('172.31.0.2', 8080, '/en/', 2)]
    before = len(calls)
    for ip, path in [('example.org', '/en/'), ('127.0.0.1', '/en/'), ('172.31.0.2', '//foreign')]:
        with pytest.raises(DockerError): port.get(ip, 8080, path, timeout=2)
    assert len(calls) == before
    def failure(*args): raise OSError('SECRET RESPONSE')
    port.exchange = failure
    with pytest.raises(DockerError, match='^production-http-read-unavailable$'):
        port.get('172.31.0.2', 8080, '/en/', timeout=2)


def test_caddy_config_constraints_never_claim_runtime_acceptance():
    from test_production_ports import sample
    evidence, snapshot = sample()
    runtime = CaddyRuntime(evidence.reference, snapshot.version, paths())
    config = {'Entrypoint': None, 'Cmd': COMMAND, 'User': '', 'WorkingDir': '/srv',
              'Env': ['XDG_CONFIG_HOME=/config', 'XDG_DATA_HOME=/data', 'CADDY_VERSION=v'+runtime.version]}
    evidence = replace(evidence, defaults_json=encode(config))
    assert 'isolated-caddy-read-only-capabilities-storage-test-required' in inspect_caddy_contract(runtime, evidence)
    for key, value in [('Entrypoint', ['caddy']), ('Cmd', ['run']), ('User', '101:101'), ('Env', []), ('WorkingDir', '/')]:
        with pytest.raises(DockerError):
            inspect_caddy_contract(runtime, replace(evidence, defaults_json=encode({**config, key: value})))
    with pytest.raises(DockerError, match='attestor-unavailable'):
        ManagedGatewayReadPort(object(), image_evidence=evidence, expected_version=runtime.version).read()


def test_empty_managed_gateway_and_default_canonical_lease_gate():
    from test_production_ports import sample
    from scripts.production_integration.gateway import ProductionGateway
    image, snapshot = sample()
    snapshot = replace(snapshot, routes=(), active_caddyfile=content(()))
    class Transport:
        def read_active(self): return snapshot
    class Attestor:
        def verify(self, *args): return args[-1]
    port = ManagedGatewayReadPort(Transport(), image_evidence=image, expected_version=snapshot.version, attestor=Attestor())
    assert port.read() == ()
    with pytest.raises(DockerError):
        ProductionGateway(port).activate(None, None, 'project-router', ready=True, lease=LockLease())


def test_authenticated_source_verifier_integrates_with_oci_byte_chain():
    from test_production_integration import artifact_fixture
    from scripts.production_integration.artifacts import verify_artifact
    reference, blobs, _, _, config_id, manifest_id = artifact_fixture()
    _, facts, verifier = release_fixture()
    reference = candidate().image.split('@')[0] + '@' + reference.split('@')[1]
    verifier.candidate = replace(candidate(), image=reference)
    facts = replace(facts, publication=(PRODUCER, verifier.candidate.producer_sha, 'refs/heads/main', reference),
                    provenance=(reference, PRODUCER, verifier.candidate.producer_sha, facts.provenance[3]),
                    candidate_hash=verifier.candidate.identity)
    verifier.collector.collect_authenticated = lambda *args: facts
    request = SourceRequest(PRODUCER, candidate().producer_sha, candidate().manager_sha, reference, ORIGIN)
    evidence = verify_artifact(reference, reference.split('@')[0], blobs, request, verifier,
                               expected_source=PRODUCER, expected_origin=ORIGIN)
    assert evidence.image_id == config_id and evidence.manifest_digest == manifest_id
    verifier.collector = None
    with pytest.raises(DockerError):
        verify_artifact(reference, reference.split('@')[0], blobs, request, verifier,
                        expected_source=PRODUCER, expected_origin=ORIGIN)
