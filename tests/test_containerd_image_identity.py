"""Offline OCI-to-Engine regressions for legacy and containerd image stores."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from scripts.application_acceptance import runtime_verification
from scripts.docker_discovery import discover, container_runtime
from scripts.production_integration.artifacts import EngineBindingError, bind_engine_image
from scripts.production_integration.caddy_image import bind_caddy_engine
from scripts.production_integration.caddy_runtime import verify_caddy_runtime
from scripts.production_integration import first_phase
from scripts.reconciliation import encode
from test_caddy_integration import setup_runtime, engine_document
from test_production_integration import artifact_fixture, verify, state, router, registration


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline test attempted live IO')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def descriptor(digest, *, index=False):
    return {'digest': digest, 'size': 512,
            'mediaType': 'application/vnd.oci.image.index.v1+json' if index else
                         'application/vnd.oci.image.manifest.v1+json'}


def image_fixture(index=False, target='manifest'):
    ref, blobs, source, verifier, _, _ = artifact_fixture(index)
    evidence = verify(ref, blobs, source, verifier)
    digest = ref.split('@')[1] if target == 'index' else evidence.manifest_digest
    doc = {'Id': digest, 'Descriptor': descriptor(digest, index=target == 'index'),
           'RepoDigests': [ref], 'Os': 'linux', 'Architecture': 'amd64',
           'Config': json.loads(evidence.defaults_json)}
    return evidence, doc


@pytest.mark.parametrize('index,target', [(False, 'manifest'), (True, 'manifest'), (True, 'index')])
@pytest.mark.parametrize('docker_media_type', [False, True])
def test_containerd_image_uses_verified_target_without_rewriting_config_digest(index, target, docker_media_type):
    evidence, doc = image_fixture(index, target)
    if docker_media_type:
        doc['Descriptor']['mediaType'] = ('application/vnd.docker.distribution.manifest.list.v2+json'
                                         if target == 'index' else 'application/vnd.docker.distribution.manifest.v2+json')
    before = deepcopy(doc)
    config_digest = evidence.image_id
    assert config_digest != doc['Id']
    assert bind_engine_image(evidence, doc) is evidence
    assert evidence.image_id == config_digest and doc == before


@pytest.mark.parametrize('change', ['no-descriptor', 'wrong-descriptor', 'wrong-id', 'wrong-type',
    'malformed-type', 'zero-size', 'bool-size', 'oversized', 'wrong-platform', 'wrong-repo',
    'foreign-config', 'unverified', 'config-media-type', 'index-as-manifest'])
def test_containerd_image_rejects_incomplete_or_unbound_projection(change):
    evidence, doc = image_fixture(True, 'index')
    if change == 'no-descriptor': doc.pop('Descriptor')
    elif change == 'wrong-descriptor': doc['Descriptor']['digest'] = 'sha256:' + 'f'*64
    elif change == 'wrong-id': doc['Id'] = evidence.manifest_digest
    elif change == 'wrong-type': doc['Descriptor']['mediaType'] = 'unknown'
    elif change == 'malformed-type': doc['Descriptor']['mediaType'] = []
    elif change == 'zero-size': doc['Descriptor']['size'] = 0
    elif change == 'bool-size': doc['Descriptor']['size'] = True
    elif change == 'oversized': doc['Descriptor']['size'] = 1024*1024+1
    elif change == 'wrong-platform': doc['Architecture'] = 'arm64'
    elif change == 'wrong-repo': doc['RepoDigests'] = ['ghcr.io/foreign/app@' + doc['Id']]
    elif change == 'foreign-config': doc['Config']['User'] = '0:0'
    elif change == 'unverified': evidence = replace(evidence, verified=False)
    elif change == 'config-media-type': doc['Descriptor']['mediaType'] = 'application/vnd.oci.image.config.v1+json'
    else: doc['Descriptor']['mediaType'] = 'application/vnd.oci.image.manifest.v1+json'
    with pytest.raises(EngineBindingError):
        bind_engine_image(evidence, doc)


@pytest.mark.parametrize('target', ['config', 'manifest', 'index'])
def test_containerd_application_runtime_retains_selected_manifest(router, target):
    artifact, resource, image = state(router)
    image = replace(image, manifest_digest='sha256:' + 'e'*64)
    facts = resource.facts
    facts['imageId'] = {'config': image.image_id, 'manifest': image.manifest_digest,
                        'index': image.reference.split('@')[1]}[target]
    raw = {'Name': resource.name, 'Id': resource.identifier, 'Image': facts['imageId'],
           'ImageManifestDescriptor': descriptor(image.manifest_digest), 'Config': facts['config'],
           'HostConfig': facts['host'], 'Mounts': facts['mounts'], 'State': facts['state'],
           'NetworkSettings': {'Networks': facts['networks']}}
    adapter = SimpleNamespace(inventory=lambda kind: [raw] if kind == 'container' else [])
    observed = discover(adapter, ())
    assert observed.complete and len(observed.resources) == 1
    assert container_runtime(observed.resources[0], artifact, 'web', image)
    assert runtime_verification(raw, artifact, 'web', image).matched
    raw['ImageManifestDescriptor']['digest'] = 'sha256:' + 'f'*64
    assert not container_runtime(discover(adapter, ()).resources[0], artifact, 'web', image)
    assert not runtime_verification(raw, artifact, 'web', image).matched


@pytest.mark.parametrize('target', ['manifest', 'index'])
@pytest.mark.parametrize('change', ['missing', 'foreign-manifest', 'foreign-id', 'index-descriptor', 'arm-platform'])
def test_container_runtime_rejects_ambiguous_index_and_foreign_manifest(router, target, change):
    artifact, resource, image = state(router)
    image = replace(image, manifest_digest='sha256:' + 'e'*64)
    facts = resource.facts
    facts['imageId'] = image.manifest_digest if target == 'manifest' else image.reference.split('@')[1]
    facts['imageManifestDescriptor'] = descriptor(image.manifest_digest)
    if change == 'missing': facts.pop('imageManifestDescriptor')
    elif change == 'foreign-manifest': facts['imageManifestDescriptor']['digest'] = 'sha256:' + 'f'*64
    elif change == 'foreign-id': facts['imageId'] = 'sha256:' + 'f'*64
    elif change == 'index-descriptor': facts['imageManifestDescriptor'] = descriptor(image.reference.split('@')[1], index=True)
    else: facts['imageManifestDescriptor']['platform'] = {'os': 'linux', 'architecture': 'arm64'}
    assert not container_runtime(replace(resource, facts_json=encode(facts)), artifact, 'web', image)


def test_caddy_image_and_runtime_accept_containerd_and_preserve_security(tmp_path):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    image = {'Id': evidence.manifest_digest, 'Descriptor': descriptor(evidence.manifest_digest),
             'RepoDigests': [evidence.reference], 'Os': 'linux', 'Architecture': 'amd64',
             'Config': json.loads(evidence.defaults_json)}
    assert bind_caddy_engine(runtime, evidence, image) is evidence
    doc = engine_document(artifact, evidence)
    doc['Image'] = evidence.reference.split('@')[1]
    doc['ImageManifestDescriptor'] = descriptor(evidence.manifest_digest)
    assert verify_caddy_runtime(runtime, artifact, evidence, doc) == ()
    doc['ImageManifestDescriptor']['digest'] = 'sha256:' + 'f'*64
    assert 'caddy-runtime-image-mismatch' in verify_caddy_runtime(runtime, artifact, evidence, doc)
    doc['ImageManifestDescriptor'] = descriptor(evidence.manifest_digest)
    doc['HostConfig']['Privileged'] = True
    assert 'caddy-runtime-isolation-mismatch' in verify_caddy_runtime(runtime, artifact, evidence, doc)


@pytest.mark.parametrize('cached', [False, True])
def test_bootstrap_caddy_cache_uses_real_binding_after_pull(tmp_path, monkeypatch, cached):
    runtime, _, evidence = setup_runtime(tmp_path)
    present, calls = [cached], []
    doc = {'Id': evidence.manifest_digest, 'Descriptor': descriptor(evidence.manifest_digest),
           'RepoDigests': [evidence.reference], 'Os': 'linux', 'Architecture': 'amd64',
           'Config': json.loads(evidence.defaults_json)}
    def listing(args):
        return json.dumps({'Repository': evidence.reference.split('@')[0],
                           'Digest': evidence.reference.split('@')[1], 'ID': doc['Id']}).encode() if present[0] else b''
    def inspect(kind, reference):
        assert present[0] and (kind, reference) == ('image', evidence.reference)
        calls.append('inspect')
        return doc
    def pull(argv, **options):
        assert argv[-1] == evidence.reference and argv[-3:-1] == ['--platform', 'linux/amd64']
        calls.append('pull')
        present[0] = True
    monkeypatch.setattr('scripts.docker_adapter.run_bounded', pull)
    candidate = SimpleNamespace(host='a'*32, daemon='daemon')
    prepared = SimpleNamespace(candidate=candidate)
    grant = SimpleNamespace(candidate=candidate, require=lambda: None)
    engine = SimpleNamespace(identity=lambda: (candidate.host, candidate.daemon), _run=listing, inspect=inspect)
    resources = first_phase.BootstrapResources(SimpleNamespace(verify=lambda: None), engine,
                                               None, None, None, None, None)
    resources.prepared, resources.runtime, resources.evidence = prepared, runtime, evidence
    resources.prepare_image(prepared, runtime, grant)
    assert calls.count('pull') == (0 if cached else 1)
    assert calls.count('inspect') >= 2
