"""Offline boundaries only; synthetic admin publications are never production evidence."""
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts.docker_adapter import DockerError
from scripts.docker_discovery import ImageEvidence
from scripts.production_integration import administrative_review as ar
from scripts.production_integration.candidate import hashed
from scripts.production_integration.host_policy import HostPolicy
from scripts.production_integration.installation import SealedInstallation
from scripts.production_integration.local_sources import ReadOnlyJournalSource
from scripts.reconciliation import encode


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def forbidden(*a, **kw): pytest.fail('live IO attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def review(monkeypatch):
    seal = SealedInstallation('a'*40, ())
    monkeypatch.setattr(SealedInstallation, 'verify', lambda self: None)
    monkeypatch.setattr(ar.time, 'time', lambda: 10)
    policy = HostPolicy('b'*32, 'daemon', *(['sha256:'+'c'*64]*3), 1024**3, 100000,
                        'restore-review', 'console-review', 'allocation-review', 'budget-review', 'isolation-review')
    runtime = type('Runtime', (), {'image': 'ghcr.io/example/caddy@sha256:'+'d'*64,
                  'render': lambda self: type('Artifact', (), {'model': {'image': self.image}})()})()
    image = ImageEvidence(runtime.image, 'sha256:'+'e'*64, 'linux/amd64', 'sha256:'+'f'*64, True, '{}')
    document = dict(version=1, phase='empty-host', manager_sha=seal.manager_sha, host_policy=asdict(policy),
        not_before=1, expires=100, administrator='portfolio_admin', decision_id='1'*32,
        caddy_runtime_hash=hashed(runtime.render().model), caddy_evidence_hash=hashed(asdict(image)),
        runtime_acceptance='independent-exact-digest-acceptance-review', rollout=None)
    monkeypatch.setattr(ar, 'read_review', lambda: document.copy())
    return seal, document, runtime, image


def test_two_independent_ports_preserve_objects_and_recheck_revocation(review):
    seal, doc, runtime, image = review
    port = ar.AdministrativePhaseReview(seal)
    identity = (port.policy.host, port.policy.daemon)
    assert port.verify(port.policy, identity) is port.policy
    assert port.verify(runtime, image, identity) is image
    with pytest.raises(DockerError): port.verify(replace(port.policy, storage_budget='other'), identity)
    with pytest.raises(DockerError): port.verify(runtime, replace(image, verified=False), identity)
    with pytest.raises(DockerError): port.verify(runtime, image, ('other', 'daemon'))
    doc['decision_id'] = '2'*32
    with pytest.raises(DockerError, match='revoked'): port.current()


@pytest.mark.parametrize('case', ['unknown', 'bool', 'source', 'phase', 'expired', 'future', 'long',
                                'host', 'policy', 'capacity', 'inodes', 'backup', 'artifact', 'acceptance', 'ids'])
def test_missing_or_unapproved_fields_fail_closed(review, case):
    seal, d, _, _ = review
    if case == 'unknown': d['approved'] = True
    elif case == 'bool': d['version'] = True
    elif case == 'source': d['manager_sha'] = '0'*40
    elif case == 'phase': d['phase'] = 'auto'
    elif case == 'expired': d['expires'] = 10
    elif case == 'future': d['not_before'] = 11
    elif case == 'long': d['expires'] = 3602
    elif case == 'host': d['host_policy']['host'] = ''
    elif case == 'policy': d['host_policy']['network_hash'] = ''
    elif case == 'capacity': d['host_policy']['minimum_bytes'] = 1
    elif case == 'inodes': d['host_policy']['minimum_inodes'] = True
    elif case == 'backup': d['host_policy']['backup_restore'] = ''
    elif case == 'artifact': d['caddy_evidence_hash'] = 'anything'
    elif case == 'acceptance': d['runtime_acceptance'] = ''
    elif case == 'ids': d['rollout'] = {'container_id': '1'*64}
    with pytest.raises(DockerError): ar.AdministrativePhaseReview(seal)


def test_rollout_requires_real_journal_success_and_exact_host_source(review, monkeypatch):
    seal, d, _, _ = review
    d['phase'] = 'rollout'
    d['rollout'] = dict(container_id='1'*64, proxy_id='2'*64, private_id='3'*64,
        infrastructure=[['container', '1'*64, 'portfolio-gateway-caddy-1', 'sha256:'+'4'*64],
                        ['network', '2'*64, 'portfolio-proxy', 'sha256:'+'5'*64],
                        ['network', '3'*64, 'portfolio-project-router-private', 'sha256:'+'6'*64]])
    port = ar.AdministrativePhaseReview(seal)
    state = dict(incomplete=[], authorizations=[], bootstrap={'gateway': 'lkg'},
        bootstrapAuthorization={'state': 'succeeded', 'payload': encode(dict(scope='first-bootstrap',
            host=port.policy.host, daemon=port.policy.daemon, manager_sha=seal.manager_sha))})
    lease = object()
    def read(self, actual):
        assert actual is lease
        return state
    monkeypatch.setattr(ReadOnlyJournalSource, 'read', read)
    assert port.rollout_pins(ReadOnlyJournalSource(), lease) == d['rollout']
    for key, bad in [('incomplete', ['unfinished']), ('authorizations', ['unresolved']),
                     ('bootstrap', None), ('bootstrapAuthorization', {'state': 'blocked'})]:
        saved = state[key]
        state[key] = bad
        with pytest.raises(DockerError): port.rollout_pins(ReadOnlyJournalSource(), lease)
        state[key] = saved
    state['bootstrapAuthorization']['payload'] = encode(dict(scope='first-bootstrap', host='wrong',
        daemon=port.policy.daemon, manager_sha=seal.manager_sha))
    with pytest.raises(DockerError): port.rollout_pins(ReadOnlyJournalSource(), lease)


def test_protected_read_is_bounded_canonical_and_private(tmp_path, monkeypatch):
    path = tmp_path / 'review'
    path.write_bytes(encode({'v': 1}).encode())
    path.chmod(0o600)
    monkeypatch.setattr(ar, 'REVIEW', str(path))
    monkeypatch.setattr(ar.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(ar, 'protected', lambda p: None)
    # Fixture owner substitutes only metadata UID/GID; real protection tested by
    # SealedInstallation suites. No root-owned production file is created here.
    original_fstat, original_lstat = ar.os.fstat, Path.lstat
    def root_info(info):
        values = list(info)
        values[4] = values[5] = 0
        return ar.os.stat_result(values)
    monkeypatch.setattr(ar.os, 'fstat', lambda fd: root_info(original_fstat(fd)))
    monkeypatch.setattr(Path, 'lstat', lambda self: root_info(original_lstat(self)))
    assert ar.read_review() == {'v': 1}
    for data, mode in [(b'{"v": 1}', 0o600), (b'{}', 0o644), (b'x'*(ar.LIMIT+1), 0o600),
                       (b'{"v":1,"v":1}', 0o600)]:
        path.write_bytes(data)
        path.chmod(mode)
        with pytest.raises((DockerError, ValueError)): ar.read_review()


def test_shipped_bindings_still_raise():
    for filename, function in [('installation/bindings.py', 'build'),
                               ('installation/admin_bindings.py', 'authenticate_initial_installation')]:
        spec = importlib.util.spec_from_file_location('blocked_binding', filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with pytest.raises((RuntimeError, DockerError)): getattr(module, function)(None)


def test_admin_file_consumption_is_exclusive_and_checks_actual_intake(tmp_path, monkeypatch):
    import hashlib
    from installation import admin_bindings as admin, operator_bootstrap as helper
    monkeypatch.setitem(sys.modules, 'operator_bootstrap', helper)
    monkeypatch.setattr(helper, 'admin_context', lambda: None)
    monkeypatch.setattr(helper, 'metadata', lambda *a, **kw: None)
    intake = tmp_path / 'bundle.zip'
    intake.write_bytes(b'synthetic bundle, never installed')
    monkeypatch.setattr(helper, 'INPUT', intake)
    helpers = Path('/usr/local/lib/portfolio-deployment-bootstrap')
    # Actual helper path keys are required by authenticate_decision. Reads are
    # redirected to private fixture files, never written to the system.
    pins = []
    original_open = Path.open
    files = {}
    for name in ('operator_bootstrap.py', 'admin_bindings.py'):
        path = helpers / name
        fixture = tmp_path / name
        fixture.write_bytes(name.encode())
        files[path] = fixture
        pins.append((str(path), hashlib.sha256(name.encode()).hexdigest()))
    monkeypatch.setattr(Path, 'open', lambda p, *a, **kw: original_open(files.get(p, p), *a, **kw))
    machine = 'b'*32
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: machine if str(p) == '/etc/machine-id'
                        else original_read(p, *a, **kw))
    monkeypatch.setattr(admin.time, 'time', lambda: 10)
    pins = tuple(sorted(pins))
    decision = admin.InitialInstallationDecision(machine, 'a'*40, helper.digest(intake.read_bytes()),
        dict(pins)[str(helpers / 'operator_bootstrap.py')], dict(pins)[str(helpers / 'admin_bindings.py')],
        helper.digest(helper.canonical(list(map(list, pins)))), pins, '1'*32, 1, 100)
    decision_path = tmp_path / 'installation-decision.json'
    decision_path.write_bytes(helper.canonical(asdict(decision)))
    approval = admin.authenticated_file_decision(decision.bundle_sha256)
    assert approval.host_id == machine
    marker = tmp_path / ('spent-' + decision.decision_id)
    before = marker.read_bytes()
    with pytest.raises(FileExistsError): admin.authenticated_file_decision(decision.bundle_sha256)
    assert marker.read_bytes() == before
    marker.unlink()  # Test fixture only: production never removes spent markers.
    intake.write_bytes(b'changed')
    with pytest.raises(helper.InstallError): admin.authenticated_file_decision(decision.bundle_sha256)
    assert not marker.exists()


def test_phase_recipe_selects_only_sealed_ports_and_measured_pins(review, monkeypatch):
    from contextlib import contextmanager
    from scripts.production_integration import phase_bindings as pb
    seal, document, runtime, image = review
    seen = {}
    def first(installation, **kw):
        assert installation is seal
        seen.update(kw)
        return 'bootstrap-executor'
    monkeypatch.setattr(pb, 'build_first_phase', first)
    assert pb.build_reviewed_phase(seal, empty_host={'fixed_port': 'sealed'}, rollout={}) == 'bootstrap-executor'
    assert seen['fixed_port'] == 'sealed'
    assert seen['prerequisites'] is seen['caddy_acceptance']
    assert seen['host_policy'] == seen['prerequisites'].policy
    document['phase'] = 'rollout'
    document['rollout'] = dict(container_id='1'*64, proxy_id='2'*64, private_id='3'*64,
        infrastructure=[['container', '1'*64, 'gateway', 'sha256:'+'4'*64],
                        ['network', '2'*64, 'proxy', 'sha256:'+'5'*64],
                        ['network', '3'*64, 'private', 'sha256:'+'6'*64]])
    # Journal gate is covered separately with the actual ReadOnlyJournalSource
    # type. This fixture isolates dispatch arguments and canonical lease use.
    lease = object()
    @contextmanager
    def lock(): yield lease
    monkeypatch.setattr(pb, 'production_host_lock', lock)
    def pins(self, journal, actual):
        assert type(journal) is ReadOnlyJournalSource and actual is lease
        return self.document['rollout']
    monkeypatch.setattr(ar.AdministrativePhaseReview, 'rollout_pins', pins)
    monkeypatch.setattr(pb, 'build_rollout', first)
    from scripts.production_integration.bootstrap import BootstrapNetworkContract
    from scripts.production_integration.docker import EngineContract
    # EngineContract is intentionally only sealed trusted wiring here, not JSON.
    engine = type('EnginePin', (), {'host_id': 'b'*32, 'daemon_id': 'daemon'})()
    fixed = dict(bootstrap_proxy=BootstrapNetworkContract('172.30.240.0/24', '172.30.240.1'),
                 engine_contract=engine, caddy=runtime, caddy_image=image)
    assert pb.build_reviewed_phase(seal, empty_host={}, rollout=fixed) == 'bootstrap-executor'
    assert seen['active_pins'] == ('1'*64, '2'*64)
    assert seen['private_network_id'] == '3'*64
    assert seen['proxy'].network_id == '2'*64
    assert seen['active_channel'] is seen['gateway_runtime'] is None
