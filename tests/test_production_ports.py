"""Read-only production transport ports exercised entirely with synthetic data."""
from dataclasses import replace
import copy

import pytest

from scripts.compose import LABEL
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import ImageEvidence
from scripts.gateway import GatewayRoute
from scripts.production_integration.gateway import content
from scripts.production_integration.gateway_read import GatewaySnapshot, ManagedGatewayReadPort
from scripts.production_integration.readiness import ProductionProbes
from test_planning import root


ID = 'a' * 64
COMPONENT = {'name': 'web', 'type': 'http', 'port': 8080, 'health': {'strategy': 'http', 'path': '/en/'}}


class Docker:
    def __init__(self):
        self.raw = {'Id': ID, 'Image': 'sha256:' + 'b' * 64, 'State': {'Status': 'running'},
                    'Config': {'Labels': {LABEL + '.managed': 'true', LABEL + '.application': 'router',
                                         LABEL + '.component': 'web', 'com.docker.compose.project': 'portfolio-router',
                                         'com.docker.compose.service': 'web'}},
                    'HostConfig': {}, 'NetworkSettings': {'Networks': {
                        'portfolio-router-private': {'IPAddress': '172.31.0.2'}}}}
        self.calls = 0

    def inspect(self, kind, identity):
        assert kind == 'container' and identity == ID
        self.calls += 1
        return copy.deepcopy(self.raw)


class Http:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return 200


def test_bounded_internal_probe_pins_id_path_and_network():
    docker, http = Docker(), Http()
    result = ProductionProbes(docker, http, 'router', '172.31.0.0/24').probe(
        COMPONENT, ID, timeout=2, follow_redirects=False)
    assert result.http_status == 200 and docker.calls == 2
    assert http.calls == [(('172.31.0.2', 8080, '/en/'), {'timeout': 2, 'follow_redirects': False})]


@pytest.mark.parametrize('case', ['owner', 'id', 'ports', 'network', 'stopped', 'timeout', 'redirect', 'replacement'])
def test_probe_rejects_unverified_targets(case):
    docker, http = Docker(), Http()
    kwargs = {'timeout': 2, 'follow_redirects': False}
    if case == 'owner':
        docker.raw['Config']['Labels'] = {}
    elif case == 'id':
        docker.raw['Id'] = 'b' * 64
    elif case == 'ports':
        docker.raw['HostConfig']['PortBindings'] = {'80/tcp': []}
    elif case == 'network':
        docker.raw['NetworkSettings']['Networks']['portfolio-router-private']['IPAddress'] = '8.8.8.8'
    elif case == 'stopped':
        docker.raw['State']['Status'] = 'exited'
        result = ProductionProbes(docker, http, 'router', '172.31.0.0/24').probe(COMPONENT, ID, **kwargs)
        assert result.running is False and not http.calls
        return
    elif case == 'timeout':
        kwargs['timeout'] = 11
    elif case == 'redirect':
        kwargs['follow_redirects'] = True
    else:
        def get(*args, **kw):
            docker.raw['Image'] = 'sha256:' + 'c' * 64
            return 200
        http.get = get
    with pytest.raises(DockerError):
        ProductionProbes(docker, http, 'router', '172.31.0.0/24').probe(COMPONENT, ID, **kwargs)


def sample():
    routes = (GatewayRoute('router', 'web', 'router.example.test', 8080, 'router-web'),)
    image = ImageEvidence('caddy@sha256:' + 'c' * 64, 'sha256:' + 'd' * 64, 'linux/amd64', None, True, '{}')
    snapshot = GatewaySnapshot(routes, content(routes), image.image_id, '2.10.2', True, True, True)
    return image, snapshot


@pytest.mark.parametrize('change', [{}, {'version': '2.9.0'}, {'ownership_verified': False},
                                  {'runtime_verified': False}, {'certificates_storage_verified': False},
                                  {'image_config_id': 'sha256:unknown'}, {'active_caddyfile': ':80 { respond ok }'}])
def test_gateway_read_requires_exact_active_managed_runtime(change):
    image, snapshot = sample()
    class Transport:
        def read_active(self):
            return replace(snapshot, **change)
    port = ManagedGatewayReadPort(Transport(), image_evidence=image, expected_version='2.10.2')
    if change:
        with pytest.raises(DockerError):
            port.read()
    else:
        assert port.read() == snapshot.routes
    with pytest.raises(DockerError, match='stage-09-4'):
        port.compare_and_reload(None, snapshot.active_caddyfile)


@pytest.mark.parametrize('field,value', [('schemaVersion', True), ('schemaVersion', 1), ('layers', None),
                                       ('layers', [{'mediaType': 'foreign', 'digest': 'sha256:' + 'a' * 64, 'size': 1}]),
                                       ('config-size', None), ('config-size', True), ('config-size', -1)])
def test_malformed_oci_descriptors_are_blockers(field, value):
    from test_production_integration import artifact_fixture, verify
    from scripts.production_integration.artifacts import blob_digest
    from scripts.reconciliation import encode
    import json
    ref, blobs, source, verifier, _, _ = artifact_fixture(index=False)
    doc = json.loads(blobs[ref.split('@')[1]])
    if field == 'config-size':
        doc['config']['size'] = value
    else:
        doc[field] = value
    raw = encode(doc).encode()
    digest = blob_digest(raw)
    blobs[digest] = raw
    ref = ref.split('@')[0] + '@' + digest
    with pytest.raises(DockerError):
        verify(ref, blobs, replace(source, reference=ref), verifier)


def test_internal_worker_rehearsal_needs_no_gateway(root, tmp_path):
    from test_planning import put
    from scripts.planning_inputs import build_desired_state
    from test_readiness_recovery import World
    from test_production_integration import rehearsal, REVISION
    put(root, 'internal-jobs')
    world = World(build_desired_state(root, REVISION).applications[0])
    results, adapter, gateway, _ = rehearsal(world, tmp_path)
    assert results[0].release_success and len(adapter.calls) == 1
    assert not gateway.calls


def test_gateway_never_activates_after_readiness_identity_changes(root, tmp_path, monkeypatch):
    from test_planning import put
    from scripts.planning_inputs import build_desired_state
    from scripts.production_integration.gateway import RehearsalLifecycle
    from test_readiness_recovery import World
    from test_production_integration import rehearsal, REVISION
    put(root, 'project-router')
    world = World(build_desired_state(root, REVISION).applications[0])
    original = RehearsalLifecycle._fresh_target
    def changed(self, app, name, *, snapshot=None):
        resource, docker = original(self, app, name, snapshot=snapshot)
        if snapshot is not None:
            resource = replace(resource, identifier='replacement-after-readiness')
        return resource, docker
    monkeypatch.setattr(RehearsalLifecycle, '_fresh_target', changed)
    results, _, gateway, _ = rehearsal(world, tmp_path)
    assert results[0].status == 'recovery-required' and not results[0].release_success
    assert not gateway.calls
