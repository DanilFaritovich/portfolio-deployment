"""Offline regressions; never instantiate a Docker transport in a unit test."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from scripts import caddy_integration as c
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import verify_oci_bytes, map_oci_bytes, blob_digest
from scripts.production_integration.caddy_image import inspect_caddy_contract
from scripts.production_integration.caddy_runtime import verify_caddy_runtime
from scripts.production_integration.runtime import CaddyRuntime
from scripts.runtime_integration import IntegrationFailure, InjectedFailure
from test_production_integration import paths


@pytest.fixture(autouse=True)
def no_commands(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline test attempted a real command')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def runner_env():
    return dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_OS='Linux',
                RUNNER_ARCH='X64', GITHUB_EVENT_NAME='pull_request',
                GITHUB_REPOSITORY='DanilFaritovich/portfolio-deployment')


@pytest.mark.parametrize('field', list(runner_env()))
def test_environment_rejects_missing_or_wrong_host(field, monkeypatch):
    monkeypatch.setattr(c.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(c.platform, 'machine', lambda: 'x86_64')
    env = runner_env()
    c.environment_guard('unix:///var/run/docker.sock', c.AUTHORIZATION, env)
    env[field] = 'wrong'
    with pytest.raises(IntegrationFailure):
        c.environment_guard('unix:///var/run/docker.sock', c.AUTHORIZATION, env)


@pytest.mark.parametrize('endpoint,authorization', [('', c.AUTHORIZATION),
    ('tcp://localhost:2375', c.AUTHORIZATION), ('unix:///tmp/docker.sock', c.AUTHORIZATION),
    ('unix:///var/run/docker.sock', ''), ('unix:///var/run/docker.sock', 'yes')])
def test_authorization_and_endpoint_fail_closed(endpoint, authorization):
    with pytest.raises(IntegrationFailure):
        c.environment_guard(endpoint, authorization, runner_env())


def test_entrypoint_without_authorization_never_contacts_docker(capsys):
    assert c.main(['--endpoint', 'unix:///var/run/docker.sock', '--authorize', '']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['stage'] == 'environment' and report['cleanup'] == 'not-started'
    assert report['productionAdmission'] is False and report['realDocker'] is False


def setup_runtime(tmp_path):
    lock, evidence = c.image_evidence()
    runtime = CaddyRuntime(evidence.reference, lock['version'], paths())
    artifact = c.isolated_artifact(runtime, tmp_path, 'a' * 16)
    return runtime, artifact, evidence


def test_locked_official_bytes_and_existing_contract(tmp_path):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    assert evidence.manifest_digest != evidence.image_id
    assert 'exact-caddy-image-provenance-required' in inspect_caddy_contract(runtime, evidence)
    service = artifact.model['services']['caddy']
    original = runtime.render().model['services']['caddy']
    for key in original.keys() - {'volumes', 'ports', 'networks', 'labels'}:
        assert service[key] == original[key]
    assert service['ports'] == []
    assert all(Path(m['source']).is_relative_to(tmp_path) for m in service['volumes'])
    assert [(m['target'], m.get('read_only', False)) for m in service['volumes']] == [
        ('/etc/caddy', True), ('/data', False), ('/config', False)]
    assert c.test_configuration().count('admin 127.0.0.1:2019') == 1
    assert 'auto_https off' in c.test_configuration()
    assert 'https://' not in c.test_configuration()
    # Registry-neutral extraction must NOT extend production/application admission.
    with pytest.raises(DockerError, match='artifact-registry-not-admitted'):
        verify_oci_bytes(evidence.reference, 'docker.io/library/caddy', {})


@pytest.mark.parametrize('name', ['index', 'manifest', 'config'])
def test_corrupt_locked_bytes_rejected(tmp_path, name):
    for file in c.FIXTURES.iterdir():
        (tmp_path / file.name).write_bytes(file.read_bytes())
    with (tmp_path / (name + '.oci')).open('ab') as f:
        f.write(b' ')
    with pytest.raises((IntegrationFailure, DockerError)):
        c.image_evidence(tmp_path)


@pytest.mark.parametrize('change', ['version', 'command', 'user', 'entrypoint', 'platform', 'unverified'])
def test_image_contract_rejects_incompatible_defaults(tmp_path, change):
    runtime, _, evidence = setup_runtime(tmp_path)
    config = json.loads(evidence.defaults_json)
    if change == 'version': config['Env'] = []
    elif change == 'command': config['Cmd'] = ['sh']
    elif change == 'user': config['User'] = '1000'
    elif change == 'entrypoint': config['Entrypoint'] = ['sh']
    elif change == 'platform': evidence = replace(evidence, platform='linux/arm64')
    else: evidence = replace(evidence, verified=False)
    with pytest.raises(DockerError):
        inspect_caddy_contract(runtime, replace(evidence, defaults_json=json.dumps(config)))


def engine_document(artifact, evidence):
    """Engine's typed API projection; absence of optional zero fields is allowed."""
    service = artifact.model['services']['caddy']
    config = json.loads(evidence.defaults_json)
    config.update(Image=evidence.reference, Labels=service['labels'])
    mounts = [{'Type': 'bind', 'Source': m['source'], 'Destination': m['target'],
               'RW': not m.get('read_only', False), 'Propagation': 'rprivate'} for m in service['volumes']]
    return {'Image': evidence.image_id, 'Config': config, 'Mounts': mounts,
            'HostConfig': {'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'CapAdd': ['CAP_NET_BIND_SERVICE'],
                'SecurityOpt': ['no-new-privileges'], 'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                'NetworkMode': artifact.model['networks']['proxy']['name'],
                'Mounts': [{'Type': 'bind', 'Source': m['Source'], 'Target': m['Destination'], 'ReadOnly': not m['RW'],
                            'BindOptions': {}} for m in mounts]},
            'NetworkSettings': {'Networks': {artifact.model['networks']['proxy']['name']: {}}}}


def test_engine_equivalent_nnp_form_and_exact_security(tmp_path):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    doc = engine_document(artifact, evidence)
    assert verify_caddy_runtime(runtime, artifact, evidence, doc) == ()
    doc['HostConfig']['SecurityOpt'] = ['no-new-privileges:true']
    doc['HostConfig']['CapAdd'] = ['NET_BIND_SERVICE']
    assert verify_caddy_runtime(runtime, artifact, evidence, doc) == ()


@pytest.mark.parametrize('field,value,code', [
    ('CapAdd', ['CAP_NET_BIND_SERVICE', 'CAP_SYS_ADMIN'], 'capabilities'),
    ('CapAdd', [], 'capabilities'), ('CapDrop', [], 'security'),
    ('SecurityOpt', ['seccomp=unconfined'], 'security'), ('ReadonlyRootfs', False, 'security'),
    ('Tmpfs', {'/tmp': 'rw'}, 'security'), ('Privileged', True, 'isolation'),
    ('Binds', ['/var/run/docker.sock:/var/run/docker.sock'], 'isolation'),
    ('PidMode', 'host', 'isolation'), ('NetworkMode', 'host', 'networks'),
    ('Devices', [{'PathOnHost': '/dev/kvm'}], 'isolation'),
    ('PortBindings', {'2019/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '2019'}]}, 'ports'),
])
def test_extra_permissions_fail_closed(tmp_path, field, value, code):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    doc = engine_document(artifact, evidence)
    doc['HostConfig'][field] = value
    assert 'caddy-runtime-' + code + '-mismatch' in verify_caddy_runtime(runtime, artifact, evidence, doc)


@pytest.mark.parametrize('change,code', [('writable-config', 'mounts'), ('extra-mount', 'mounts'),
    ('mount-request', 'mount-requests'), ('default-env', 'defaults'), ('wrong-image', 'image'),
    ('extra-network', 'networks'), ('ownership', 'ownership')])
def test_contract_drift_rejected(tmp_path, change, code):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    doc = engine_document(artifact, evidence)
    if change == 'writable-config': doc['Mounts'][0]['RW'] = True
    elif change == 'extra-mount': doc['Mounts'].append(deepcopy(doc['Mounts'][0]))
    elif change == 'mount-request': doc['HostConfig']['Mounts'][0]['ReadOnly'] = False
    elif change == 'default-env': doc['Config']['Env'].append('TOKEN=sensitive-example')
    elif change == 'wrong-image': doc['Image'] = 'sha256:' + '0' * 64
    elif change == 'extra-network': doc['NetworkSettings']['Networks']['foreign'] = {}
    else: doc['Config']['Labels'] = {}
    codes = verify_caddy_runtime(runtime, artifact, evidence, doc)
    assert 'caddy-runtime-' + code + '-mismatch' in codes
    assert 'sensitive-example' not in json.dumps(codes)


@pytest.mark.parametrize('failure', ['success', 'startup', 'cleanup'])
def test_partial_failure_cleanup_and_safe_report(monkeypatch, capsys, failure):
    calls = []
    class Fake:
        completed = []
        observations = {}
        stage = 'startup'
        def __init__(self, *args): pass
        def run(self):
            if failure == 'startup': raise IntegrationFailure('private-value-must-not-escape')
            raise InjectedFailure('injected')
        def cleanup(self):
            calls.append('cleanup')
            if failure == 'cleanup': raise IntegrationFailure('private-cleanup-error')
    monkeypatch.setattr(c, 'environment_guard', lambda *args: None)
    monkeypatch.setattr(c, 'CaddyHarness', Fake)
    assert c.main(['--endpoint', 'unused', '--authorize', c.AUTHORIZATION]) == (0 if failure == 'success' else 1)
    output = capsys.readouterr().out
    report = json.loads(output)
    assert calls == ['cleanup']
    assert 'private' not in output and report['productionAdmission'] is False
    assert report['cleanup'] == ('failed' if failure == 'cleanup' else 'passed')

@pytest.mark.parametrize('text', [
    '{ admin :2019 }\n:80 { respond 200 }',
    '{ admin 0.0.0.0:2019 }\n:80 { respond 200 }',
    'real.example.com { respond 200 }',
    '{ admin off }\n:80 { respond 200 }',
    ':80 { file_server browse }',
    c.test_configuration() + '\n:443 { respond 200 }',
])
def test_dangerous_caddy_config_rejected_before_start(text):
    c.validate_test_configuration(c.test_configuration())
    with pytest.raises(IntegrationFailure, match='caddy-test-config-not-allowlisted'):
        c.validate_test_configuration(text)


def test_available_provenance_is_linked_but_not_trusted():
    lock = json.loads((c.FIXTURES / 'image-lock.json').read_bytes())
    index = json.loads((c.FIXTURES / 'index.oci').read_bytes())
    desc = next(d for d in index['manifests'] if d['digest'] == lock['availableAttestation']['manifest'])
    raw = (c.FIXTURES / 'attestation-manifest.oci').read_bytes()
    assert len(raw) == desc['size'] and blob_digest(raw) == desc['digest']
    manifest = json.loads(raw)
    statement = (c.FIXTURES / 'provenance.oci').read_bytes()
    desc = next(d for d in manifest['layers'] if d['digest'] == blob_digest(statement))
    assert len(statement) == desc['size']
    statement = json.loads(statement)
    assert any(s['digest']['sha256'] == lock['manifest'].split(':')[1] for s in statement['subject'])
    assert statement['predicate']['builder'] == lock['availableAttestation']['builderClaim']
    assert lock['availableAttestation']['signatureVerified'] is False
    assert lock['provenance'] == 'unverified-production-blocker'

@pytest.mark.parametrize('key,value', [('read_only', False), ('cap_drop', []),
    ('security_opt', []), ('cap_add', ['SYS_ADMIN']), ('command', ['sh']),
    ('ports', ['2019:2019/tcp'])])
def test_weakened_expected_artifact_never_weakens_policy(tmp_path, key, value):
    runtime, artifact, evidence = setup_runtime(tmp_path)
    doc = engine_document(artifact, evidence)
    model = artifact.model
    model['services']['caddy'][key] = value
    weak = replace(artifact, model_json=json.dumps(model))
    assert verify_caddy_runtime(runtime, weak, evidence, doc) == ('caddy-runtime-expectation-invalid',)


def test_observed_caddy_engine_projection_and_official_alias(tmp_path):
    from scripts.production_integration.caddy_image import bind_caddy_engine
    from scripts.production_integration.artifacts import normalize_engine_config
    runtime, _, evidence = setup_runtime(tmp_path)
    report = json.loads((c.FIXTURES / 'run-39843536752-report.json').read_bytes())
    checks = report['observations']['bindingChecks']
    assert checks['imageIdMatchesConfig'] and checks['platformMatches']
    assert report['observations']['shortNameRepoDigestPresent'] is True
    assert checks['configDifferingShapes']['User'] == {'engine': 'empty-string', 'oci': 'absent'}
    assert checks['configDifferingShapes']['Entrypoint'] == {'engine': 'null', 'oci': 'absent'}
    assert not checks['configOtherFieldsDiffer']
    config = json.loads(evidence.defaults_json)
    # Synthetic API projection corroborated by retained structural diagnostics.
    projected = {**config, 'User': '', 'Entrypoint': None}
    assert normalize_engine_config(config) == normalize_engine_config(projected)
    inspected = {'Id': evidence.image_id, 'RepoDigests': ['caddy@' + runtime.image.split('@')[1]],
                 'Os': 'linux', 'Architecture': 'amd64', 'Config': projected}
    before = deepcopy(inspected)
    assert bind_caddy_engine(runtime, evidence, inspected) == evidence
    assert inspected == before  # No mutation of independently collected evidence.
    for reference in ('caddy@sha256:' + '0' * 64, 'foreign/caddy@' + runtime.image.split('@')[1],
                      'caddy:2.11.7', 'library/caddy@' + runtime.image.split('@')[1]):
        with pytest.raises(DockerError):
            bind_caddy_engine(runtime, evidence, {**inspected, 'RepoDigests': [reference]})
    for key, value in (('User', '0'), ('User', None), ('Entrypoint', []), ('Entrypoint', ['sh'])):
        with pytest.raises(DockerError):
            bind_caddy_engine(runtime, evidence, {**inspected, 'Config': {**projected, key: value}})
