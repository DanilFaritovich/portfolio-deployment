"""Finite wiring tests with controlled authorities; never production approvals."""
import hashlib
from types import SimpleNamespace
import socket

import pytest

from scripts.docker_adapter import DockerError
from scripts.production_integration import fixed_bindings as fixed
from scripts.production_integration.first_phase import EmptyHostReader, BootstrapResources
from scripts.production_integration.public_postconditions import PublicPolicy, PublicPostconditions
from scripts.production_integration.public_witness import SignedPublicWitness
from scripts.production_integration.release_transport import GitHubRead
from scripts.production_integration.release_source import WorkflowCheck


@pytest.mark.parametrize('phase', ['empty-host', 'rollout'])
def test_both_recipes_use_real_adapters_with_only_controlled_evidence(tmp_path, monkeypatch, phase):
    def forbidden(*a, **kw): pytest.fail('live network attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    token = tmp_path / 'token'
    token.write_text('test-token')
    token.chmod(0o600)
    ca = tmp_path / 'ca'
    ca.write_bytes(b'controlled-ca')
    public = PublicPolicy(('a'*32, 'daemon'), ('8.8.8.8',), (), '1.1.1.1', str(ca),
                          hashlib.sha256(ca.read_bytes()).hexdigest(), 'b'*64)
    policy = fixed.FixedPolicy(public, 'key', '172.30.240.0/24', '172.30.240.1', '172.30.241.0/24',
        1024**3, 'c'*40, 'ghcr.io/danilfaritovich/portfolio-caddy@sha256:'+'d'*64, '2.11.7', 'e'*64, 'f'*64,
        'user', '1'*40, 'ghcr.io/danilfaritovich/project-router@sha256:'+'2'*64, '1'*40,
        'https://github.com/actions/runner', WorkflowCheck(fixed.PRODUCER, 'CI', 15368, '.github/workflows/ci.yml'),
        WorkflowCheck(fixed.PRODUCER, 'Publish', 15368, '.github/workflows/ci.yml'))
    installation = SimpleNamespace(verify=lambda: None, files=((fixed.GH, 'e'*64), (fixed.ROOT, 'f'*64),
                                                             (str(ca), public.ca_sha256)))
    review = SimpleNamespace(policy=SimpleNamespace(host='a'*32, daemon='daemon'), document={'phase': phase},
                             verify=lambda runtime, evidence, identity: evidence)
    engine = SimpleNamespace(identity=lambda: public.identity)
    monkeypatch.setattr(fixed, 'TOKEN', token)
    monkeypatch.setattr(fixed, 'protected', lambda path: None)
    monkeypatch.setattr(fixed, 'AdministrativePhaseReview', lambda i: review)
    monkeypatch.setattr(fixed, 'installed_engine', lambda i, c: engine)
    monkeypatch.setattr(fixed, 'tls_context', lambda pem: None)
    monkeypatch.setattr(fixed, 'build_reviewed_phase', lambda i, **kwargs: kwargs)
    monkeypatch.setattr(fixed.CaddyReleaseCollector, 'collect', lambda self, *args: 'controlled-evidence')
    app = SimpleNamespace(id='project-router')
    monkeypatch.setattr(fixed, 'InstalledContentReader', lambda i: lambda: (SimpleNamespace(applications=(app,)),))
    # Key validation is covered by real SSHSIG tests; wire the real witness here.
    monkeypatch.setattr('scripts.production_integration.public_witness.trust_keys', lambda raw: {})
    result = fixed.build_fixed(installation, policy)
    empty = result['empty_host']
    assert isinstance(empty['github'], GitHubRead)
    assert empty['engine_contract'].endpoint == 'unix:///run/docker.sock'
    assert empty['proxy'].existing_network_id is None
    assert empty['caddy_crypto'].repository == fixed.MANAGER
    assert empty['crypto'].repository == fixed.PRODUCER
    assert empty['caddy_registry'].authenticated is True
    if phase == 'rollout':
        rollout = result['rollout']
        assert isinstance(rollout['bootstrap_reader'], EmptyHostReader)
        assert rollout['bootstrap_attestor'] is rollout['bootstrap_reader']
        assert isinstance(rollout['bootstrap_resources'], BootstrapResources)
        assert isinstance(rollout['gateway_postconditions'], PublicPostconditions)
        assert isinstance(rollout['gateway_postconditions'].external, SignedPublicWitness)
        assert rollout['image_registry'].func is fixed.project_router_image_registry
        assert 'context' in rollout['image_registry'].keywords
        assert 'infrastructure' not in rollout and 'private_network_id' not in rollout
        assert 'active_pins' not in rollout  # supplied only by reviewed phase transition


def test_missing_owner_policy_stays_closed_before_any_io(monkeypatch):
    def forbidden(*args): pytest.fail('missing policy reached installation')
    with pytest.raises(DockerError, match='owner-policy-required'):
        fixed.build_fixed(SimpleNamespace(verify=forbidden), None)
