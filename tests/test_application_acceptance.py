"""Offline acceptance orchestration and failure boundaries; no Docker daemon."""
import copy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest
import yaml

from scripts import application_acceptance as a
from scripts.acceptance_contract import candidate_model
from scripts.compose import LABEL
from scripts.docker_discovery import ImageEvidence
from scripts.production_integration.artifacts import EngineBindingError, bind_engine_image, normalize_engine_config
from scripts.gateway import desired_routes
from scripts.planning_inputs import build_desired_state
from scripts.validate_registry import RegistryValidationError, validate_registry

ROOT = Path(__file__).resolve().parents[1]
SESSION = '0123456789abcdef'

def environment():
    return {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux',
            'RUNNER_ARCH': 'X64', 'GITHUB_REPOSITORY': 'DanilFaritovich/portfolio-deployment',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/develop',
            'ACCEPTANCE_REF_PROTECTED': 'true', 'ACCEPTANCE_CONFIRM': 'isolated-acceptance',
            'GITHUB_SHA': 'a' * 40}


@pytest.mark.parametrize('field,value', [
    ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_REF', 'refs/heads/feature/unreviewed'),
    ('ACCEPTANCE_REF_PROTECTED', 'false'), ('RUNNER_ENVIRONMENT', 'self-hosted'),
    ('RUNNER_ARCH', 'ARM64'), ('ACCEPTANCE_CONFIRM', ''), ('GITHUB_SHA', 'main'),
    ('GITHUB_REPOSITORY', 'foreign/manager'), ('DOCKER_HOST', 'tcp://host:2375'),
])
def test_runner_gate_fails_closed(field, value):
    env = environment()
    a.runner_gate(env)
    env[field] = value
    with pytest.raises(a.AcceptanceError):
        a.runner_gate(env)


@pytest.mark.parametrize('version', [
    b'gh version 2.101.0 (2026-09-01)', b'gh version 2.99.0 (2026-09-01)',
    b'gh version 2.102.0-rc1 (2026-09-01)', b'gh version 2.102.1 (2026-09-01)',
    b'gh version unknown', b'', b'gh version 2.102.0',
])
def test_gh_version_fails_closed(version):
    with pytest.raises(a.AcceptanceError):
        a.verify_gh_version(version)


def gh_archive(kind='file'):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as archive:
        member = tarfile.TarInfo(a.GH_ARCHIVE.removesuffix('.tar.gz') + '/bin/gh')
        member.size = 3
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/usr/bin/gh'
        archive.addfile(member, io.BytesIO(b'bin'))
    return raw.getvalue()


@pytest.mark.parametrize('failure', ['', 'checksum', 'missing-checksum', 'download', 'version', 'symlink'])
def test_gh_provision_checks_integrity_before_execution(tmp_path, monkeypatch, failure):
    raw = gh_archive('symlink' if failure == 'symlink' else 'file')
    monkeypatch.setattr(a, 'GH_ARCHIVE_SHA256', None if failure == 'missing-checksum' else hashlib.sha256(raw).hexdigest())
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        assert not kwargs.get('gh')  # No token during download or version probe.
        if args[0] == 'curl':
            assert args[-1] == 'https://github.com/cli/cli/releases/download/v2.102.0/gh_2.102.0_linux_amd64.tar.gz'
            assert args[args.index('--proto-redir') + 1] == '=https'
            if failure == 'download':
                raise a.AcceptanceError()
            return raw + b'tampered' if failure == 'checksum' else raw
        assert args == [str(tmp_path / 'gh'), '--version']
        assert (tmp_path / 'gh').read_bytes() == b'bin'
        version = '2.101.0' if failure == 'version' else '2.102.0'
        return ('gh version ' + version + ' (2026-09-30)\nhttps://github.com/cli/cli/releases/tag/v' + version + '\n').encode()
    monkeypatch.setattr(a, 'command', command)
    if failure:
        with pytest.raises(a.AcceptanceError):
            a.provision_gh(tmp_path)
        if failure != 'version':
            assert len(calls) == 1 and not (tmp_path / 'gh').exists()
    else:
        assert a.provision_gh(tmp_path) == str(tmp_path / 'gh')
        assert (tmp_path / 'gh').stat().st_mode & 0o777 == 0o500


def test_no_redirect_credential_forwarding():
    req = a.urllib.request.Request('https://ghcr.io/blob', headers={'Authorization': 'Bearer private'})
    handler = a.RegistryRedirect()
    redirected = handler.redirect_request(req, None, 302, '', {}, 'https://pkg-containers.githubusercontent.com/blob')
    assert not redirected.has_header('Authorization')
    with pytest.raises(a.AcceptanceError):
        handler.redirect_request(req, None, 302, '', {}, 'http://evil.test/blob')


def test_workflow_dispatch_only_and_least_privilege():
    # BaseLoader preserves the YAML `on` key as a string.
    workflow = yaml.load((ROOT / '.github/workflows/application-acceptance.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['acceptance']
    assert job['runs-on'] == 'ubuntu-24.04' and int(job['timeout-minutes']) <= 15
    assert 'github.ref_protected' in job['if'] and 'refs/heads/develop' in job['if']
    assert 'environment' not in job
    steps = job['steps']
    assert any(s.get('run') == 'python -m scripts.application_acceptance' for s in steps)
    artifact = steps[-1]
    assert artifact['if'] == 'always()' and artifact['with']['path'] == 'acceptance-evidence/report.json'
    for step in steps:
        if 'uses' in step:
            assert len(step['uses'].split('@')[1]) == 40
    text = (ROOT / '.github/workflows/application-acceptance.yml').read_text()
    assert 'secrets.' not in text and 'id-token:' not in text and 'packages:' not in text


def test_gate_failure_preserves_preexisting_evidence(tmp_path, monkeypatch):
    output = tmp_path / 'acceptance-evidence'
    output.mkdir()
    previous = output / 'report.json'
    previous.write_text('existing evidence')
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert a.main() == 1
    assert previous.read_text() == 'existing evidence'


def project(tmp_path):
    return candidate_model(ROOT, tmp_path / 'registry', 'project-router', 'a' * 40, SESSION)


def test_project_router_contract(tmp_path):
    app, release, artifact = project(tmp_path)
    assert app.spec['enabled'] is False and validate_registry(ROOT).applications == ()
    assert desired_routes(build_desired_state(tmp_path / 'registry', 'a' * 40)) == ()
    identity = release['components']['web']
    assert identity['sourceSha'] == identity['signerSha'] == '526c765f33932e163392dd1d512de4aa2f2a5260'
    assert artifact.model['services']['web']['image'].endswith('a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853')
    assert artifact.model['services']['web']['user'] == '101:101'
    assert artifact.model['services']['web']['read_only'] is True
    assert artifact.model['services']['web']['networks']['proxy']['aliases'] == [SESSION + '-project-router-web']
    assert len(identity['attestations']) == 2
    assert identity['attestations'][1]['expectedPredicate']['buildArguments'] == {'VITE_SITE_ORIGIN': 'https://danil-miftakhov.dev'}
    assert all(n['name'].startswith('pd-smoke-' + SESSION) for n in artifact.model['networks'].values())


def synthetic(tmp_path, *, persistent=False):
    root = tmp_path / 'source'
    (root / 'docs/candidates').mkdir(parents=True)
    (root / 'registry/applications').mkdir(parents=True)
    (root / 'policy').mkdir()
    doc = yaml.safe_load((ROOT / 'docs/candidates/project-router.yaml').read_text())
    doc['metadata'] = {'id': 'example-backend', 'sourceRepository': 'Example/backend'}
    doc['spec'].pop('ingress')
    web = doc['spec']['components'][0]
    web['name'] = 'api'
    web['image'] = 'ghcr.io/example/api@sha256:' + 'a' * 64
    web['health']['path'] = '/ready'
    worker = copy.deepcopy(web)
    worker.update(name='worker', image='ghcr.io/example/worker@sha256:' + 'b' * 64,
                  type='worker', health={'strategy': 'imageHealthcheck'}, dependsOn=['api'])
    worker.pop('port')
    doc['spec']['components'].append(worker)
    identity = doc['release']['components']['web']
    identity['attestations'] = [{'predicateType': a.SLSA_TYPE}]
    doc['release']['components'] = {n: copy.deepcopy(identity) for n in ('api', 'worker')}
    if persistent:
        doc['spec']['storage'] = {'type': 'persistent', 'volumes': [{'name': 'data'}]}
        worker['volumeMounts'] = [{'volume': 'data', 'mountPath': '/data'}]
        doc['release']['volumePolicy'] = 'remove-isolated'
    policy = {'apiVersion': doc['apiVersion'], 'kind': 'AdmissionPolicy', 'applications': [{
        'id': 'example-backend', 'sourceRepository': 'Example/backend',
        'imageRepositories': {'api': ['ghcr.io/example/api'], 'worker': ['ghcr.io/example/worker']},
        'hostnames': [], 'platforms': ['linux/amd64'], 'acceptance': {
            'signerWorkflows': ['.github/workflows/publish.yml'], 'sourceRefs': ['refs/heads/main'],
            'requiredPredicates': [a.SLSA_TYPE], 'runtimeSecurity': {
                n: copy.deepcopy(web['runtimeSecurity']) for n in ('api', 'worker')}}}]}
    return root, doc, policy


def write_candidate(root, doc, policy):
    (root / 'docs/candidates/example-backend.yaml').write_text(yaml.safe_dump(doc))
    (root / 'policy/admission.yaml').write_text(yaml.safe_dump(policy))


def load_synthetic(tmp_path, *, persistent=False):
    root, doc, policy = synthetic(tmp_path, persistent=persistent)
    write_candidate(root, doc, policy)
    return candidate_model(root, tmp_path / 'projection', 'example-backend', 'a' * 40, SESSION)


@pytest.mark.parametrize('persistent', [False, True])
def test_multi_component_projection(tmp_path, persistent):
    app, release, artifact = load_synthetic(tmp_path, persistent=persistent)
    assert a.startup_order(app) == ('api', 'worker')
    assert artifact.model['services']['worker']['depends_on'] == {'api': {'condition': 'service_started'}}
    assert set(artifact.model['services']) == set(release['components']) == {'api', 'worker'}
    if persistent:
        assert artifact.model['volumes']['data']['name'] == 'pd-smoke-' + SESSION + '-example-backend-data'
        assert artifact.model['volumes']['data']['labels'][LABEL + '.test-session'] == SESSION


@pytest.mark.parametrize('change', ['image', 'source', 'workflow', 'ref', 'security', 'missing-release',
    'missing-policy', 'health', 'unknown-field', 'production-volume', 'storage-consent', 'secrets', 'config',
    'missing-predicate', 'unknown-release-field'])
def test_invalid_or_unsupported_contract_cannot_run(tmp_path, change):
    root, doc, policy = synthetic(tmp_path, persistent=True)
    api = doc['spec']['components'][0]
    identity = doc['release']['components']['api']
    if change == 'image': api['image'] = 'ghcr.io/evil/api@sha256:' + 'a' * 64
    if change == 'source': doc['metadata']['sourceRepository'] = 'Evil/repo'
    if change == 'workflow': identity['signerWorkflow'] = '.github/workflows/unreviewed.yml'
    if change == 'ref': identity['sourceRef'] = 'refs/heads/unreviewed'
    if change == 'security': api['runtimeSecurity']['readOnlyRootFilesystem'] = False
    if change == 'missing-release': doc.pop('release')
    if change == 'missing-policy': policy['applications'][0].pop('acceptance')
    if change == 'health': api.pop('health')
    if change == 'unknown-field': api['command'] = 'arbitrary shell'
    if change == 'production-volume': doc['spec']['storage']['volumes'][0]['external'] = True
    if change == 'storage-consent': doc['release'].pop('volumePolicy')
    if change in ('secrets', 'config'):
        field = 'secretRefs' if change == 'secrets' else 'configRefs'
        doc['spec'][field] = api[field] = ['production']
    if change == 'missing-predicate': identity['attestations'] = []
    if change == 'unknown-release-field': identity['dockerArgs'] = '--privileged'
    write_candidate(root, doc, policy)
    with pytest.raises((a.AcceptanceError, RegistryValidationError)):
        candidate_model(root, tmp_path / 'projection', 'example-backend', 'a' * 40, SESSION)


@pytest.mark.parametrize('application', ['unknown', '../../project-router', 'project-router;id', '', 'a' * 33])
def test_unknown_or_unsafe_id(tmp_path, application):
    with pytest.raises(a.AcceptanceError):
        candidate_model(ROOT, tmp_path / 'projection', application, 'a' * 40, SESSION)


@pytest.mark.parametrize('change', ['origin', 'drop', 'claim', 'security'])
def test_project_cannot_drop_release_requirements(tmp_path, change):
    root = tmp_path / 'source'
    (root / 'docs/candidates').mkdir(parents=True)
    (root / 'policy').mkdir()
    (root / 'registry/applications').mkdir(parents=True)
    doc = yaml.safe_load((ROOT / 'docs/candidates/project-router.yaml').read_text())
    identity = doc['release']['components']['web']
    if change == 'origin': identity['attestations'][1]['expectedPredicate']['buildArguments']['VITE_SITE_ORIGIN'] = 'https://wrong.test'
    if change == 'drop': identity['attestations'].pop()
    if change == 'claim': identity['attestations'][1]['expectedPredicate']['originOutputVerified'] = False
    if change == 'security': doc['spec']['components'][0]['runtimeSecurity']['user'] = '1000:1000'
    (root / 'docs/candidates/project-router.yaml').write_text(yaml.safe_dump(doc))
    (root / 'policy/admission.yaml').write_bytes((ROOT / 'policy/admission.yaml').read_bytes())
    with pytest.raises(a.AcceptanceError):
        candidate_model(root, tmp_path / 'projection', 'project-router', 'a' * 40, SESSION)


def test_attestation_binding(tmp_path):
    app, release, artifact = project(tmp_path)
    identity = release['components']['web']
    image = app.spec['components'][0]['image']
    repository, digest = image.split('@')
    for expectation in identity['attestations']:
        cmd = a.attestation_command(image, app.source_repository, identity, expectation['predicateType'], '/verified/gh')
        for flag, value in [('--source-digest', identity['sourceSha']), ('--signer-digest', identity['signerSha']),
                            ('--source-ref', identity['sourceRef']), ('--repo', app.source_repository),
                            ('--signer-workflow', 'github.com/' + app.source_repository + '/' + identity['signerWorkflow'])]:
            assert cmd[cmd.index(flag) + 1] == value
        assert '--deny-self-hosted-runners' in cmd
        statement = {'predicateType': expectation['predicateType'], 'predicate': copy.deepcopy(expectation.get('expectedPredicate', {})),
                     'subject': [{'name': repository, 'digest': {'sha256': digest[7:]}}]}
        raw = json.dumps([{'verificationResult': {'statement': statement}}]).encode()
        assert a.verified_statement(raw, image, expectation) == statement
        with pytest.raises(a.AcceptanceError):
            a.verified_statement(raw, repository + '@sha256:' + 'a' * 64, expectation)
        if 'expectedPredicate' in expectation:
            statement['predicate']['runAttempt'] = '2'
            with pytest.raises(a.AcceptanceError):
                a.verified_statement(json.dumps([{'verificationResult': {'statement': statement}}]).encode(), image, expectation)


def fixture_evidence(app):
    return {c['name']: SimpleNamespace(reference=c['image'], manifest_digest='sha256:' + 'c' * 64,
            image_id='sha256:' + 'd' * 64, defaults_json=json.dumps({'Healthcheck': {'Test': ['CMD', '/health']}}))
            for c in app.spec['components']}


def binding_fixture():
    proof = ImageEvidence('ghcr.io/fixtures/app@sha256:' + 'a' * 64,
                          'sha256:' + 'b' * 64, 'linux/amd64', 'sha256:' + 'c' * 64,
                          True, a.encode({'User': '101:101', 'Cmd': ['/app'],
                                          'Healthcheck': {'Test': ['CMD', '/health']}}))
    inspected = {'Id': proof.image_id, 'RepoDigests': [proof.reference], 'Os': 'linux',
                 'Architecture': 'amd64', 'Config': json.loads(proof.defaults_json)}
    return proof, inspected


def test_retained_first_run_cannot_identify_failing_substep():
    # Exact sanitized artifact 12446746614; ZIP SHA256 verified before retention.
    report = json.loads((ROOT / 'tests/fixtures/acceptance/run-39825828078-report.json').read_text())
    assert report['stage'] == 'pull-engine-binding' and report['result'] == 'failed'
    assert report['productionAdmission'] is False
    record = report['components']['web']
    assert len(record['attestations']) == 2
    assert record['image'].split('@')[1] != record['mapping']['manifest'] != record['mapping']['config']
    assert 'engine' not in record and 'errorCodes' not in report


@pytest.mark.parametrize('change,code', [
    ('id', 'engine-image-id-mismatch'), ('repo', 'engine-repo-digests-mismatch'),
    ('platform', 'engine-platform-mismatch'), ('config', 'engine-config-mismatch'),
    ('all', None),
])
def test_binding_classifies_every_failed_check_without_values(change, code):
    proof, inspected = binding_fixture()
    if change in ('id', 'all'): inspected['Id'] = proof.manifest_digest
    if change in ('repo', 'all'): inspected['RepoDigests'] = ['private-value']
    if change in ('platform', 'all'): inspected['Architecture'] = 'private-value'
    if change in ('config', 'all'):
        inspected['Config'].update(User='0', Env=['SECRET=private-value'], Labels={'private-value': 'secret'},
                                   **{'private-value': 'secret'})
    with pytest.raises(EngineBindingError) as caught:
        bind_engine_image(proof, inspected)
    error = caught.value
    if code:
        assert error.codes == (code,)
    else:
        assert len(error.codes) == 4 and error.checks['imageIdMatchesManifest']
    if change in ('config', 'all'):
        assert error.checks['configDifferingFields'] == ['User', 'Env', 'Labels']
        assert error.checks['configOtherFieldsDiffer']
    assert 'private-value' not in json.dumps(error.checks) + str(error)


@pytest.mark.parametrize('digests,accepted,manifest_present', [
    ('requested', True, False), ('both', True, True), ('manifest', False, True),
    ('foreign', False, False), ('empty', False, False), ('null', False, False),
    ('string', False, False),
])
def test_index_and_platform_repo_digests_are_not_interchangeable(digests, accepted, manifest_present):
    proof, inspected = binding_fixture()
    manifest_ref = proof.reference.split('@')[0] + '@' + proof.manifest_digest
    inspected['RepoDigests'] = {'requested': [proof.reference], 'both': [proof.reference, manifest_ref],
        'manifest': [manifest_ref], 'foreign': ['ghcr.io/foreign/app@' + proof.reference.split('@')[1]],
        'empty': [], 'null': None, 'string': proof.reference}[digests]
    if accepted:
        assert bind_engine_image(proof, inspected) == proof
    else:
        with pytest.raises(EngineBindingError) as caught:
            bind_engine_image(proof, inspected)
        assert caught.value.codes == ('engine-repo-digests-mismatch',)
        assert caught.value.checks['repoDigestsContainPlatformManifest'] is manifest_present


@pytest.mark.parametrize('field,value', [
    ('Entrypoint', None), ('Labels', None), ('Volumes', {}), ('WorkingDir', ''),
    ('AttachStdout', True), ('Env', []), ('ArgsEscaped', False),
])
def test_engine_config_representation_differences_remain_fail_closed(field, value):
    # PR #19's conservative cases outside the explicitly supported projection.
    proof, inspected = binding_fixture()
    inspected['Config'][field] = value
    with pytest.raises(EngineBindingError) as caught:
        bind_engine_image(proof, inspected)
    assert caught.value.codes == ('engine-config-mismatch',)
    assert caught.value.checks['configDifferingFields'] == [field]
    assert caught.value.checks['configNullOrOmittedOnly'] is (value is None)


ENGINE_PROJECTION_DEFAULTS = {
    'Volumes': None, 'OnBuild': None, 'Hostname': '', 'Domainname': '',
    'AttachStdin': False, 'AttachStdout': False, 'AttachStderr': False,
    'Tty': False, 'OpenStdin': False, 'StdinOnce': False, 'Image': '',
}


def test_retained_second_run_and_authenticated_oci_structure():
    report = json.loads((ROOT / 'tests/fixtures/acceptance/run-39828163158-report.json').read_text())
    structure = json.loads((ROOT / 'tests/fixtures/acceptance/run-39828163158-oci-config-shapes.json').read_text())
    web = report['components']['web']
    checks = web['engine']['checks']
    assert web['mapping']['config'] == structure['configDigest']
    assert report['errorCodes'] == ['engine-config-mismatch']
    assert checks['imageIdMatchesConfig'] and checks['repoDigestsContainRequested'] and checks['platformMatches']
    assert set(checks['configDifferingFields']) == set(ENGINE_PROJECTION_DEFAULTS)
    assert all(structure['shapes'][k] == 'absent' for k in ENGINE_PROJECTION_DEFAULTS)
    assert not checks['configOtherFieldsDiffer'] and not checks['configNullOrOmittedOnly']
    # The historical Engine values were NOT retained. Model only Moby's documented
    # zero-value projection; never relabel this synthetic Config as captured inspect.
    proof, inspected = binding_fixture()
    inspected['Config'].update(ENGINE_PROJECTION_DEFAULTS)
    assert bind_engine_image(proof, inspected) == proof


@pytest.mark.parametrize('field,value', ENGINE_PROJECTION_DEFAULTS.items())
@pytest.mark.parametrize('reverse', [False, True])
def test_only_documented_image_projection_defaults_are_equivalent(field, value, reverse):
    proof, inspected = binding_fixture()
    baseline = copy.deepcopy(inspected['Config'])
    projected = {**baseline, field: value}
    if reverse:
        proof = replace(proof, defaults_json=a.encode(projected))
    else:
        inspected['Config'] = projected
    assert bind_engine_image(proof, inspected) == proof
    assert normalize_engine_config(projected) == baseline
    assert projected == {**baseline, field: value}  # No mutation of authenticated defaults.


@pytest.mark.parametrize('field,value', [
    ('User', '0'), ('Entrypoint', ['/other']), ('Cmd', ['/other']), ('Env', ['SECRET=private-value']),
    ('WorkingDir', '/other'), ('Healthcheck', {'Test': ['NONE']}),
    ('Healthcheck', {'Test': ['CMD', '/health'], 'Interval': 123}),
    ('Volumes', {'/data': {}}), ('ExposedPorts', {'80/tcp': {}}),
    ('Labels', {'secret-label': 'private-value'}), ('StopSignal', 'SIGKILL'),
    ('ArgsEscaped', True), ('Shell', ['/bin/sh']), ('StopTimeout', 0),
    ('OnBuild', ['RUN private-value']), ('Hostname', 'private-value'), ('Domainname', 'private-value'),
    ('Image', 'private-value'), ('AttachStdin', True), ('AttachStdout', True), ('AttachStderr', True),
    ('Tty', True), ('OpenStdin', True), ('StdinOnce', True),
    ('NetworkDisabled', True), ('MacAddress', 'private-value'),
])
def test_projection_never_hides_meaningful_image_changes(field, value):
    proof, inspected = binding_fixture()
    inspected['Config'].update(ENGINE_PROJECTION_DEFAULTS)
    inspected['Config'][field] = value
    with pytest.raises(EngineBindingError) as caught:
        bind_engine_image(proof, inspected)
    assert caught.value.codes == ('engine-config-mismatch',)
    assert field in caught.value.checks['configDifferingFields']
    assert 'private-value' not in json.dumps(caught.value.checks)


@pytest.mark.parametrize('field,before,after', [
    ('User', '101:101', '101'), ('Entrypoint', ['/app'], []), ('Cmd', ['/app'], []),
    ('Env', ['A=1', 'B=2'], ['B=2', 'A=1']), ('Env', ['A=1'], ['A=2']),
    ('Healthcheck', {'Test': ['CMD', '/health'], 'Timeout': 100}, {'Test': ['CMD', '/health'], 'Timeout': 101}),
    ('Volumes', {'/data': {}}, {}), ('ExposedPorts', {'80/tcp': {}}, {}),
    ('OnBuild', ['RUN /build'], None), ('StopTimeout', 10, 0),
])
def test_config_removals_and_runtime_changes_are_strict(field, before, after):
    proof, inspected = binding_fixture()
    proof = replace(proof, defaults_json=a.encode({**inspected['Config'], field: before}))
    inspected['Config'].update(ENGINE_PROJECTION_DEFAULTS)
    inspected['Config'][field] = after
    with pytest.raises(EngineBindingError):
        bind_engine_image(proof, inspected)


@pytest.mark.parametrize('field,value', [
    ('Volumes', {}), ('OnBuild', []), ('Hostname', None), ('AttachStdin', None),
    ('Volumes', []), ('OnBuild', {}), ('Tty', 0), ('Image', False),
    ('User', []), ('Entrypoint', '/app'), ('Cmd', [1]), ('Env', {}),
    ('Healthcheck', []), ('Healthcheck', {'Test': '/health'}),
    ('Healthcheck', {'Retries': True}), ('Healthcheck', {'private-value': 1}),
    ('Volumes', {'/data': None}), ('ExposedPorts', {'80/tcp': True}),
    ('Labels', {'secret': 123}), ('StopTimeout', False), ('private-value', None),
])
def test_unsupported_config_representations_block(field, value):
    proof, inspected = binding_fixture()
    inspected['Config'][field] = value
    with pytest.raises(EngineBindingError) as caught:
        bind_engine_image(proof, inspected)
    assert caught.value.codes == ('engine-config-mismatch',)
    assert 'private-value' not in json.dumps(caught.value.checks)


@pytest.mark.parametrize('config', [None, [], '', False, {'User': []}, {'Volumes': []},
    {'Tty': 0}, {'private-value': 'secret'}, {'Healthcheck': {'private-value': 'secret'}}])
def test_even_identical_unsupported_configs_block(config):
    proof, inspected = binding_fixture()
    proof = replace(proof, defaults_json=a.encode(config))
    inspected['Config'] = config
    with pytest.raises(EngineBindingError):
        bind_engine_image(proof, inspected)


@pytest.mark.parametrize('change,code', [
    ('id', 'engine-image-id-mismatch'), ('repo', 'engine-repo-digests-mismatch'),
    ('platform', 'engine-platform-mismatch'),
])
def test_projection_preserves_identity_guards(change, code):
    proof, inspected = binding_fixture()
    inspected['Config'].update(ENGINE_PROJECTION_DEFAULTS)
    if change == 'id': inspected['Id'] = proof.manifest_digest
    if change == 'repo': inspected['RepoDigests'] = [proof.reference.split('@')[0] + '@' + proof.manifest_digest]
    if change == 'platform': inspected['Os'] = 'windows'
    with pytest.raises(EngineBindingError) as caught:
        bind_engine_image(proof, inspected)
    assert caught.value.codes == (code,)
    assert caught.value.checks['configMatches']


def test_structural_diagnostics_are_bounded_and_never_copy_config_data(tmp_path, monkeypatch):
    proof, inspected = binding_fixture()
    inspected['Config'].update(ENGINE_PROJECTION_DEFAULTS)
    inspected['Config'].update(User='private-user', Env=['SECRET=private-env'],
        Labels={'private-label': 'private-value'}, Healthcheck={'Test': ['CMD', 'private-command']},
        **{'private-field': 'private-value'})
    monkeypatch.setattr(a, 'command', lambda *args, **kwargs: json.dumps([inspected]).encode())
    report, record = {}, {}
    with pytest.raises(a.AcceptanceError):
        a.pull_and_bind(proof, record, report)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps({'record': record, 'report': report}))
    raw = path.read_text()
    assert 'private-' not in raw and 'SECRET=' not in raw
    checks = record['engine']['checks']
    assert checks['configDifferingShapes']['Volumes'] == {'oci': 'absent', 'engine': 'null'}
    assert checks['configDifferingShapes']['AttachStdout'] == {'oci': 'absent', 'engine': 'false'}
    assert checks['configDifferingShapes']['Image'] == {'oci': 'absent', 'engine': 'empty-string'}
    assert checks['configDifferingShapes']['Env'] == {'oci': 'absent', 'engine': 'array'}
    assert checks['configOtherFieldsDiffer']


@pytest.mark.parametrize('change', ['', 'User', 'Entrypoint', 'Cmd', 'Env', 'WorkingDir',
    'Healthcheck', 'Volumes', 'ExposedPorts', 'image-id', 'image-ref', 'privileged', 'caps', 'rootfs'])
def test_projected_binding_then_runtime_still_enforces_defaults_and_security(tmp_path, change):
    app, release, artifact = project(tmp_path)
    service = artifact.model['services']['web']
    proof = ImageEvidence(service['image'], 'sha256:' + 'b' * 64, 'linux/amd64', 'sha256:' + 'c' * 64,
        True, a.encode({'User': service['user'], 'Entrypoint': ['/app'], 'Cmd': ['serve'],
            'Env': ['APP=1'], 'WorkingDir': '/app', 'Healthcheck': {'Test': ['CMD', '/health']}}))
    image_inspect = {'Id': proof.image_id, 'RepoDigests': [proof.reference], 'Os': 'linux',
        'Architecture': 'amd64', 'Config': {**json.loads(proof.defaults_json), **ENGINE_PROJECTION_DEFAULTS}}
    assert bind_engine_image(proof, image_inspect) == proof
    runtime = a.DisposableRuntime(app, artifact, tmp_path, SESSION)
    document = MockDocker(runtime, {'web': proof}).document('web')
    document['Config'].update({k: v for k, v in ENGINE_PROJECTION_DEFAULTS.items() if k != 'Image'})
    if change in {'User', 'WorkingDir'}: document['Config'][change] = 'private-value'
    if change in {'Entrypoint', 'Cmd', 'Env'}: document['Config'][change] = ['private-value']
    if change == 'Healthcheck': document['Config'][change] = {'Test': ['NONE']}
    if change == 'Volumes': document['Config'][change] = {'/data': {}}
    if change == 'ExposedPorts': document['Config'][change] = {'80/tcp': {}}
    if change == 'image-id': document['Image'] = proof.manifest_digest
    if change == 'image-ref': document['Config']['Image'] = 'other-image'
    if change == 'privileged': document['HostConfig']['Privileged'] = True
    if change == 'caps': document['HostConfig']['CapAdd'] = ['SYS_ADMIN']
    if change == 'rootfs': document['HostConfig']['ReadonlyRootfs'] = False
    assert a.runtime_matches(document, artifact, 'web', proof) is (not change)


@pytest.mark.parametrize('failure,stage,code', [
    ('pull', 'docker-pull', 'docker-pull-failed'),
    ('inspect', 'docker-image-inspect', 'docker-image-inspect-failed'),
    ('parse', 'docker-image-inspect', 'docker-image-inspect-invalid'),
    ('shape', 'docker-image-inspect', 'docker-image-inspect-invalid'),
    ('binding', 'engine-identity-binding', 'engine-image-id-mismatch'),
    ('unexpected', 'engine-identity-binding', 'engine-binding-failed'),
])
def test_pull_inspect_binding_failures_stop_before_runtime(tmp_path, monkeypatch, failure, stage, code):
    app, release, artifact = load_synthetic(tmp_path)
    evidence, calls, command = mock_verification(monkeypatch, app)
    proof, inspected = binding_fixture()
    inspected['Id'] = 'wrong' if failure == 'binding' else proof.image_id
    # Keep signed preflight mocks, but exercise the real binding boundary.
    monkeypatch.setattr(a, 'bind_engine_image', bind_engine_image)
    for item in evidence.values():
        item.image_id = proof.image_id
        item.defaults_json = proof.defaults_json
    def fail_command(args, **kwargs):
        if args[:2] == ['docker', 'pull'] and failure == 'pull':
            raise TimeoutError('SECRET=private-value')
        if args[:3] == ['docker', 'image', 'inspect']:
            calls.append(args)
            if failure == 'inspect': raise RuntimeError('SECRET=private-value')
            if failure == 'parse': return b'private-value'
            if failure == 'shape': return b'[{}, {}]'
            inspected['RepoDigests'] = [args[-1]]
            return json.dumps([inspected]).encode()
        return command(args, **kwargs)
    monkeypatch.setattr(a, 'command', fail_command)
    if failure == 'unexpected':
        def broken(*args): raise ValueError('SECRET=private-value')
        monkeypatch.setattr(a, 'bind_engine_image', broken)
    def no_runtime(*args): pytest.fail('runtime must not be constructed')
    monkeypatch.setattr(a, 'DisposableRuntime', no_runtime)
    report = {'productionAdmission': False}
    with pytest.raises(a.AcceptanceError):
        a.accept(app, release, artifact, tmp_path, SESSION, '/verified/gh', report)
    assert report['stage'] == stage and report['errorCodes'] == [code]
    engines = [record['engine'] for record in report['components'].values() if 'engine' in record]
    assert len(engines) == 1
    engine = engines[0]
    assert list(engine.values()).count('failed') == 1
    if failure == 'pull': assert engine['inspect'] == engine['binding'] == 'pending'
    elif stage == 'docker-image-inspect': assert engine['pull'] == 'passed' and engine['binding'] == 'pending'
    else: assert engine['pull'] == engine['inspect'] == 'passed'
    assert 'private-value' not in json.dumps(report)
    assert 'cleanup' not in report


def test_main_persists_only_safe_diagnostics(tmp_path, monkeypatch):
    app, release, artifact = project(tmp_path)
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    for key, value in environment().items(): monkeypatch.setenv(key, value)
    monkeypatch.setattr(a, 'candidate_model', lambda *args: (app, release, artifact))
    monkeypatch.setattr(a, 'provision_gh', lambda *args: '/verified/gh')
    def fail(*args, **kwargs): raise RuntimeError('SECRET=private-value')
    monkeypatch.setattr(a, 'command', fail)
    def accept(app, release, artifact, directory, session, gh, report):
        report['components'] = {'web': {}}
        a.pull_and_bind(binding_fixture()[0], report['components']['web'], report)
    monkeypatch.setattr(a, 'accept', accept)
    assert a.main() == 1
    raw = (tmp_path / 'acceptance-evidence/report.json').read_text()
    report = json.loads(raw)
    assert report['result'] == 'failed' and report['productionAdmission'] is False
    assert report['errorCodes'] == ['docker-pull-failed'] and report['stage'] == 'docker-pull'
    assert 'private-value' not in raw and 'RuntimeError' not in raw


def mock_verification(monkeypatch, app):
    evidence = fixture_evidence(app)
    monkeypatch.setattr(a, 'registry_blobs', lambda image: ({}, next(e for e in evidence.values() if e.reference == image)))
    monkeypatch.setattr(a, 'bind_engine_image', lambda *args: None)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if args[0] == '/verified/gh':
            image = args[args.index('verify') + 1].removeprefix('oci://')
            predicate = args[args.index('--predicate-type') + 1]
            return json.dumps([{'verificationResult': {'statement': {'predicateType': predicate,
                'subject': [{'name': image.split('@')[0], 'digest': {'sha256': image.split('@')[1][7:]}}]}}}]).encode()
        if args[:3] == ['docker', 'image', 'inspect']: return b'[{}]'
        return b''
    monkeypatch.setattr(a, 'command', command)
    return evidence, calls, command


@pytest.mark.parametrize('failure', ['signature', 'digest', 'missing-health', 'anonymous-volume'])
def test_verification_failure_precedes_all_runtime_mutations(tmp_path, monkeypatch, failure):
    app, release, artifact = load_synthetic(tmp_path)
    evidence, calls, command = mock_verification(monkeypatch, app)
    def fail(*args, **kwargs): raise a.AcceptanceError()
    if failure == 'signature': monkeypatch.setattr(a, 'command', fail)
    if failure == 'digest': monkeypatch.setattr(a, 'registry_blobs', fail)
    if failure == 'missing-health': evidence['worker'].defaults_json = '{}'
    if failure == 'anonymous-volume': evidence['api'].defaults_json = '{"Volumes":{"/data":{}}}'
    with pytest.raises(a.AcceptanceError):
        a.accept(app, release, artifact, tmp_path, SESSION, '/verified/gh', {})
    assert all(c[0] != 'docker' for c in calls)


@pytest.mark.parametrize('failure', ['', 'start', 'readiness', 'restart', 'cleanup'])
def test_complete_multi_component_orchestration_and_failures(tmp_path, monkeypatch, failure):
    app, release, artifact = load_synthetic(tmp_path)
    mock_verification(monkeypatch, app)
    calls = []
    class Runtime:
        def __init__(self, *args): pass
        def step(self, name):
            calls.append(name)
            if failure == name: raise a.AcceptanceError('private data')
        def start(self, evidence): self.step('start')
        def verify(self, evidence): self.step('readiness')
        def restart(self, evidence): self.step('restart')
        def cleanup(self): self.step('cleanup')
    monkeypatch.setattr(a, 'DisposableRuntime', Runtime)
    report = {}
    if failure:
        with pytest.raises(a.AcceptanceError):
            a.accept(app, release, artifact, tmp_path, SESSION, '/verified/gh', report)
    else:
        a.accept(app, release, artifact, tmp_path, SESSION, '/verified/gh', report)
    assert calls[-1] == 'cleanup'
    assert report['cleanup'] == ('failed' if failure == 'cleanup' else 'passed')
    assert 'private data' not in json.dumps(report)


@pytest.mark.parametrize('kind', ['container', 'network', 'volume'])
def test_cleanup_refuses_foreign_resources(tmp_path, monkeypatch, kind):
    app, release, artifact = load_synthetic(tmp_path, persistent=True)
    runtime = a.DisposableRuntime(app, artifact, tmp_path, SESSION)
    runtime.active = True
    monkeypatch.setattr(runtime, 'owned', lambda k: ['foreign'] if k == kind else [])
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        assert args[1] == 'inspect'
        return b'[{"Config":{"Labels":{}},"Labels":{},"Name":"production"}]'
    monkeypatch.setattr(runtime, 'docker', docker)
    with pytest.raises(a.AcceptanceError): runtime.cleanup()
    assert not any('rm' in call for call in calls)


@pytest.mark.parametrize('kind', ['network', 'volume', 'container'])
def test_preflight_collision_never_mutates_or_cleans(tmp_path, monkeypatch, kind):
    app, release, artifact = load_synthetic(tmp_path, persistent=True)
    runtime = a.DisposableRuntime(app, artifact, tmp_path, SESSION)
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if kind == 'container' and args[0] == 'ps': return b'preexisting'
        if args[0] == kind:
            return next(iter(artifact.model[kind + 's'].values()))['name'].encode()
        return b''
    monkeypatch.setattr(runtime, 'docker', docker)
    with pytest.raises(a.AcceptanceError): runtime.start({})
    runtime.cleanup()
    assert runtime.active is False
    assert not any('rm' in c or 'up' in c or 'create' in c for c in calls)


class MockDocker:
    """Stateful Engine/Compose wire mock; real runtime inspection is exercised."""
    def __init__(self, runtime, evidence, failure=''):
        self.runtime, self.evidence, self.failure = runtime, evidence, failure
        self.docs = {}
        self.networks = {}
        self.volumes = {}
        self.calls = []
        self.generation = 0

    def document(self, name):
        r = self.runtime
        service = r.artifact.model['services'][name]
        proof = self.evidence[name]
        defaults = json.loads(proof.defaults_json)
        host = {'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                'RestartPolicy': {'Name': service['restart']},
                'Tmpfs': dict(t.split(':', 1) for t in service.get('tmpfs', []))}
        labels = {**service['labels'], 'com.docker.compose.service': name,
                  'com.docker.compose.project': r.artifact.project}
        networks = {r.artifact.model['networks'][key]['name']: {
            'Aliases': [name] + options.get('aliases', []), 'IPAddress': '172.22.0.' + str(2 + list(r.components).index(name))}
            for key, options in service['networks'].items()}
        mounts = [{'Type': 'tmpfs', 'Destination': k} for k in host['Tmpfs']]
        mounts += [{'Type': 'volume', 'Name': r.artifact.model['volumes'][m['source']]['name'],
                    'Destination': m['target'], 'RW': True} for m in service.get('volumes', [])]
        return {'Id': name + '-id', 'Name': name, 'Image': proof.image_id,
                'Config': {**defaults, 'Image': service['image'], 'User': service['user'], 'Labels': labels},
                'HostConfig': host, 'NetworkSettings': {'Networks': networks, 'Ports': {}}, 'Mounts': mounts,
                'State': {'Running': True, 'Health': {'Status': 'healthy'}, 'Pid': 1234,
                          'StartedAt': str(self.generation)}}

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        r = self.runtime
        if args[0] == 'compose':
            command = args[args.index(str(r.file)) + 1:]
            if command[0] == 'config': return b''
            if command[0] == 'up':
                name = command[-1]
                self.docs[name] = self.document(name)
                for kind, table in [('networks', self.networks), ('volumes', self.volumes)]:
                    for resource in r.artifact.model.get(kind, {}).values():
                        if resource.get('external'): continue
                        table[resource['name']] = {'Name': resource['name'], 'Containers': {}, 'Labels': {
                            **resource.get('labels', {}), 'com.docker.compose.project': r.artifact.project}}
                if self.failure == 'start': raise a.AcceptanceError()
                return b''
            if command[0] == 'restart':
                if self.failure == 'restart': raise a.AcceptanceError()
                self.generation += 1
                self.docs = {n: self.document(n) for n in self.docs}
                return b''
        if args[0] == 'ps':
            names = list(self.docs)
            service_filters = [x for x in args if x.startswith('label=com.docker.compose.service=')]
            if service_filters: names = [n for n in names if n == service_filters[0].split('=')[-1]]
            return ' '.join(n + '-id' for n in names).encode()
        if args[0] == 'diff': return b''
        kind, operation = args[:2]
        table = self.docs if kind == 'container' else self.networks if kind == 'network' else self.volumes
        if operation == 'create':
            labels = dict(args[i + 1].split('=', 1) for i, value in enumerate(args) if value == '--label')
            table[args[-1]] = {'Name': args[-1], 'Labels': labels, 'Containers': {}}
            return args[-1].encode()
        if operation == 'ls':
            return '\n'.join(table).encode()
        if operation == 'inspect':
            key = args[2].removesuffix('-id') if kind == 'container' else args[2]
            return json.dumps([table[key]]).encode()
        if operation == 'rm':
            if self.failure == 'cleanup': raise a.AcceptanceError()
            key = args[-1].removesuffix('-id') if kind == 'container' else args[-1]
            del table[key]
            return b''
        pytest.fail('unexpected Docker operation: ' + repr(args))


@pytest.mark.parametrize('application,failure', [('backend', f) for f in
    ('', 'start', 'restart', 'cleanup', 'ports', 'security', 'network')] + [('project-router', '')])
def test_real_runtime_with_mocked_engine(tmp_path, monkeypatch, application, failure):
    from scripts import readiness
    app, release, artifact = project(tmp_path) if application == 'project-router' else load_synthetic(tmp_path, persistent=True)
    evidence = fixture_evidence(app)
    for proof in evidence.values(): proof.verified = True
    runtime = a.DisposableRuntime(app, artifact, tmp_path, SESSION)
    engine = MockDocker(runtime, evidence, failure)
    monkeypatch.setattr(runtime, 'docker', engine.docker)
    monkeypatch.setattr(a, 'request', lambda *args, **kwargs: True)
    clock = [0]
    def verify(*args, **kwargs):
        return readiness.verify_readiness(*args, **kwargs, clock=lambda: clock[0],
            sleep=lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(a, 'verify_readiness', verify)
    namespace_calls = []
    def command(args, **kwargs):
        assert args[:2] == ['sudo', '-n']
        if args[2] == 'nsenter':
            namespace_calls.append(args)
            assert args[-3] == 'api' and args[-1] == '8080'
            assert '--net' in args and '--mount' not in args
            if failure == 'network': raise a.AcceptanceError()
            return b''
        if args[-1].endswith('/mounts'):
            return b'tmpfs /tmp tmpfs rw,noexec,nosuid,nodev,size=16384k 0 0\n'
        return (b'Uid: 101 101 101 101\nGid: 101 101 101 101\nCapEff: 0\nCapPrm: 0\nCapBnd: 0\nCapAmb: 0\nNoNewPrivs: '
                + (b'0' if failure == 'security' else b'1') + b'\n')
    monkeypatch.setattr(a, 'command', command)
    def exercise():
        runtime.start(evidence)
        if failure == 'ports': engine.docs['api']['HostConfig']['PortBindings'] = {'8080/tcp': [{'HostPort': '80'}]}
        runtime.verify(evidence)
        runtime.restart(evidence)
    if failure and failure != 'cleanup':
        with pytest.raises(a.AcceptanceError): exercise()
    else:
        exercise()
    if failure == 'cleanup':
        with pytest.raises(a.AcceptanceError): runtime.cleanup()
        assert engine.docs
    else:
        runtime.cleanup()
        assert not engine.docs and not engine.networks and not engine.volumes
    if not failure:
        assert engine.generation == 1
        if application == 'backend': assert namespace_calls
        assert [c[-1] for c in engine.calls if c[0] == 'compose' and 'up' in c] == list(a.startup_order(app))


@pytest.mark.parametrize('failure', ['', 'blocked', 'start', 'restart', 'cleanup'])
def test_public_entry_point_report_is_fail_closed(tmp_path, monkeypatch, failure):
    app, release, artifact = load_synthetic(tmp_path)
    for key, value in environment().items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv('ACCEPTANCE_APPLICATION', 'example-backend')
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    def candidate(*args):
        if failure == 'blocked': raise a.AcceptanceBlocked('private diagnostic')
        return app, release, artifact
    monkeypatch.setattr(a, 'candidate_model', candidate)
    monkeypatch.setattr(a, 'provision_gh', lambda directory: '/verified/gh')
    mock_verification(monkeypatch, app)
    class Runtime:
        def __init__(self, *args): pass
        def start(self, *args):
            if failure == 'start': raise a.AcceptanceError('private diagnostic')
        def verify(self, *args): pass
        def restart(self, *args):
            if failure == 'restart': raise a.AcceptanceError('private diagnostic')
        def cleanup(self):
            if failure == 'cleanup': raise a.AcceptanceError('private diagnostic')
    monkeypatch.setattr(a, 'DisposableRuntime', Runtime)
    assert a.main() == (1 if failure else 0)
    raw = (tmp_path / 'acceptance-evidence/report.json').read_text()
    report = json.loads(raw)
    assert report['result'] == ('blocked' if failure == 'blocked' else 'failed' if failure else 'passed')
    assert report['productionAdmission'] is False
    assert 'private diagnostic' not in raw and len(raw) < a.LIMIT


@pytest.mark.parametrize('failure', ['', 'wrong-ip', 'rcode', 'truncated'])
def test_fixed_network_probe_offline(monkeypatch, failure):
    import socket
    import struct
    import sys
    connections = []
    class Socket:
        def settimeout(self, seconds): assert seconds == 5
        def sendto(self, query, address):
            assert address == ('127.0.0.11', 53)
            self.query = query
        def recv(self, limit):
            assert limit == 4096
            header = b'\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00'
            if failure == 'rcode': header = header[:3] + b'\x83' + header[4:]
            if failure == 'truncated': return header[:4]
            ip = '172.22.0.9' if failure == 'wrong-ip' else '172.22.0.2'
            return header + self.query[12:] + b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(ip)
    class Connection:
        def __enter__(self): pass
        def __exit__(self, *args): pass
    def connect(address, timeout):
        connections.append(address)
        assert timeout == 5
        return Connection()
    monkeypatch.setattr(socket, 'socket', lambda *args: Socket())
    monkeypatch.setattr(socket, 'create_connection', connect)
    monkeypatch.setattr(sys, 'argv', ['probe', 'api', '172.22.0.2', '8080'])
    if failure:
        with pytest.raises((AssertionError, struct.error)): exec(a.NETWORK_PROBE, {})
        assert not connections
    else:
        exec(a.NETWORK_PROBE, {})
        assert connections == [('172.22.0.2', 8080)]


def test_predicate_equality_preserves_json_types():
    image = 'ghcr.io/example/api@sha256:' + 'a' * 64
    statement = {'predicateType': a.SLSA_TYPE, 'subject': [{'name': 'ghcr.io/example/api', 'digest': {'sha256': 'a' * 64}}],
                 'predicate': {'verified': 1}}
    raw = json.dumps([{'verificationResult': {'statement': statement}}]).encode()
    with pytest.raises(a.AcceptanceError):
        a.verified_statement(raw, image, {'predicateType': a.SLSA_TYPE, 'expectedPredicate': {'verified': True}})
    with pytest.raises(a.AcceptanceError):
        a.verified_statement(b'[]', image, {'predicateType': a.SLSA_TYPE})
    with pytest.raises(a.AcceptanceError):
        a.verified_statement(json.dumps([{'verificationResult': {'statement': statement}}] * 2).encode(), image,
                             {'predicateType': a.SLSA_TYPE})


@pytest.mark.parametrize('case', ['symlink', 'duplicate', 'enabled'])
def test_candidate_identity_boundaries(tmp_path, case):
    root, doc, policy = synthetic(tmp_path)
    write_candidate(root, doc, policy)
    path = root / 'docs/candidates/example-backend.yaml'
    if case == 'symlink':
        moved = tmp_path / 'declaration.yaml'
        path.rename(moved)
        path.symlink_to(moved)
    if case == 'duplicate':
        (root / 'registry/applications/example-backend.yaml').write_bytes(path.read_bytes())
    if case == 'enabled':
        doc['spec']['enabled'] = True
        write_candidate(root, doc, policy)
    with pytest.raises((a.AcceptanceError, RegistryValidationError)):
        candidate_model(root, tmp_path / 'projection', 'example-backend', 'a' * 40, SESSION)


@pytest.mark.parametrize('failure', ['', 'missing', 'value', 'type'])
def test_generic_policy_predicate_claims(tmp_path, failure):
    root, doc, policy = synthetic(tmp_path)
    constraint = {'predicateType': a.SLSA_TYPE, 'expectedPredicate': {'approvedBuild': True}}
    policy['applications'][0]['acceptance']['predicateClaims'] = [constraint]
    for identity in doc['release']['components'].values():
        identity['attestations'][0]['expectedPredicate'] = {'approvedBuild': True, 'releaseId': 'example-release'}
    expectation = doc['release']['components']['api']['attestations'][0]
    if failure == 'missing': expectation.pop('expectedPredicate')
    if failure == 'value': expectation['expectedPredicate']['approvedBuild'] = False
    if failure == 'type': expectation['expectedPredicate']['approvedBuild'] = 1
    write_candidate(root, doc, policy)
    if failure:
        with pytest.raises(a.AcceptanceError):
            candidate_model(root, tmp_path / 'projection', 'example-backend', 'a' * 40, SESSION)
    else:
        app, release, artifact = candidate_model(root, tmp_path / 'projection', 'example-backend', 'a' * 40, SESSION)
        assert app.id == 'example-backend'


@pytest.mark.parametrize('failure,step,code', [
    ('resource', 'resource-preflight', 'resource-preflight-failed'),
    ('config', 'compose-preflight', 'compose-preflight-failed'),
    ('network', 'network-create', 'network-create-failed'),
    ('up', 'compose-up', 'compose-up-failed'),
    ('up-timeout', 'compose-up', 'compose-up-timeout'),
    ('discovery', 'container-discovery', 'container-discovery-failed'),
    ('inspect', 'container-inspect', 'container-inspect-failed'),
    ('inspect-timeout', 'container-inspect', 'container-inspect-timeout'),
    ('ownership', 'ownership', 'ownership-failed'),
    ('identity', 'container-identity', 'container-identity-failed'),
    ('contract', 'runtime-contract', 'runtime-contract-failed'),
    ('malformed-contract', 'runtime-contract', 'runtime-contract-failed'),
    ('state', 'container-state', 'container-not-running'),
    ('malformed-state', 'container-state', 'container-state-failed'),
    ('health', 'docker-health', 'docker-health-unhealthy'),
    ('malformed-health', 'docker-health', 'docker-health-failed'),
    ('starting', 'readiness', 'readiness-timeout'),
    ('http', 'readiness', 'readiness-timeout'),
    ('http-timeout', 'readiness', 'readiness-timeout'),
    ('http-unavailable', 'readiness', 'readiness-timeout'),
    ('http-contract', 'http-readiness', 'http-readiness-failed'),
    ('address', 'http-readiness', 'http-readiness-failed'),
    ('late-probe', 'readiness', 'readiness-timeout'),
])
def test_runtime_substage_diagnostics_and_partial_cleanup(tmp_path, monkeypatch, failure, step, code):
    from scripts import readiness
    app, release, artifact = project(tmp_path)
    evidence = fixture_evidence(app)
    for proof in evidence.values(): proof.verified = True
    runtime = a.DisposableRuntime(app, artifact, tmp_path, SESSION)
    engine = MockDocker(runtime, evidence)
    clock = [0]
    inspections = [0]
    def docker(*args, **kwargs):
        if failure == 'resource' and args[:2] == ('network', 'ls'):
            raise RuntimeError('secret stderr env body')
        if args[0] == 'compose':
            operation = args[args.index(str(runtime.file)) + 1]
            if failure == 'config' and operation == 'config':
                raise RuntimeError('secret stderr env body')
        raw = engine.docker(*args, **kwargs)
        # Fail AFTER mutation to prove partial network/Compose resources clean up.
        if (failure == 'network' and args[:2] == ('network', 'create') or
            failure in ('up', 'up-timeout') and args[0] == 'compose' and 'up' in args):
            if failure == 'up-timeout': raise a.subprocess.TimeoutExpired('secret command', 120, output=b'secret')
            raise RuntimeError('secret stderr env body')
        if args[0] == 'ps' and any(x.startswith('label=com.docker.compose.service=') for x in args):
            if failure == 'discovery': return b''
        if args[:2] == ('container', 'inspect'):
            inspections[0] += 1
            if failure == 'inspect': return b'[{"secret":"not inspect"},{}]'
            if failure == 'inspect-timeout': raise TimeoutError('secret')
            doc = json.loads(raw)[0]
            if failure == 'ownership': doc['Config']['Labels'][LABEL + '.managed'] = 'secret'
            if failure == 'identity' and inspections[0] > 1: doc['Id'] = 'replacement'
            if failure == 'contract': doc['HostConfig']['Privileged'] = True
            if failure == 'malformed-contract': del doc['HostConfig']
            if failure == 'state': doc['State'].update(Running=False, Status='exited', ExitCode=137, OOMKilled=True)
            if failure == 'malformed-state': doc['State']['Running'] = 'secret'
            if failure in ('health', 'starting', 'malformed-health'):
                doc['State']['Health']['Status'] = {'health': 'unhealthy', 'starting': 'starting', 'malformed-health': 'secret'}[failure]
            if failure == 'address':
                for network in doc['NetworkSettings']['Networks'].values(): network['IPAddress'] = '127.0.0.1'
            return json.dumps([doc]).encode()
        return raw
    monkeypatch.setattr(runtime, 'docker', docker)
    def request(*args, **kwargs):
        if failure == 'http-timeout': raise TimeoutError('secret')
        if failure == 'http-unavailable': raise ConnectionRefusedError('secret')
        if failure == 'http-contract': raise a.AcceptanceError('secret body')
        if failure == 'late-probe': clock[0] += 121
        return failure != 'http'
    monkeypatch.setattr(a, 'request', request)
    def verify(*args, **kwargs):
        return readiness.verify_readiness(*args, **kwargs, clock=lambda: clock[0],
            sleep=lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(a, 'verify_readiness', verify)
    with pytest.raises(a.RuntimeFailure): runtime.start(evidence)
    diagnostic = copy.deepcopy(runtime.diagnostics)
    assert diagnostic['step'] == step and diagnostic['errorCode'] == code
    assert diagnostic['steps'][step] == 'failed'
    assert 0 <= diagnostic['probes'] <= 61 and 0 <= diagnostic['consecutive'] <= 2
    if failure in ('ownership', 'identity', 'contract', 'malformed-contract', 'health', 'malformed-health'):
        assert diagnostic['probes'] <= 1 and clock[0] == 0
    if failure == 'state':
        assert (diagnostic['containerState'], diagnostic['exitCode'], diagnostic['oomKilled']) == ('exited', 137, True)
    if failure in ('http', 'http-timeout', 'http-unavailable'):
        assert diagnostic['httpReadiness'] == {'http': 'not-ready', 'http-timeout': 'timeout', 'http-unavailable': 'unavailable'}[failure]
    assert 'secret' not in json.dumps(diagnostic) and len(json.dumps(diagnostic)) < 4096
    # Restore transport only: original inspected ownership gates still govern cleanup.
    monkeypatch.setattr(runtime, 'docker', engine.docker)
    runtime.cleanup()
    assert not engine.docs and not engine.networks and not engine.volumes
    assert runtime.diagnostics == diagnostic
    if failure in ('resource', 'config'):
        assert runtime.active is False
        assert not any(c[0] == 'compose' and 'up' in c or c[:2] == ('network', 'create') for c in engine.calls)


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_accept_preserves_runtime_diagnosis_even_when_cleanup_fails(tmp_path, monkeypatch, cleanup_fails):
    app, release, artifact = load_synthetic(tmp_path, persistent=True)
    mock_verification(monkeypatch, app)
    original = a.DisposableRuntime
    engines = []
    def runtime_factory(*args):
        runtime = original(*args)
        engine = MockDocker(runtime, fixture_evidence(app), 'start')
        engines.append(engine)
        def docker(*cmd, **kwargs):
            if cleanup_fails and cmd[:2] == ('container', 'rm'): raise RuntimeError('secret cleanup')
            return engine.docker(*cmd, **kwargs)
        monkeypatch.setattr(runtime, 'docker', docker)
        return runtime
    monkeypatch.setattr(a, 'DisposableRuntime', runtime_factory)
    report = {}
    with pytest.raises(RuntimeError if cleanup_fails else a.RuntimeFailure):
        a.accept(app, release, artifact, tmp_path, SESSION, '/verified/gh', report)
    assert report['stage'] == 'runtime-start' and report['cleanup'] == ('failed' if cleanup_fails else 'passed')
    assert report['errorCodes'] == ['compose-up-failed']
    assert report['runtime']['step'] == 'compose-up'
    assert 'secret' not in json.dumps(report)
    assert bool(engines[0].docs) is cleanup_fails
    if not cleanup_fails:
        assert not engines[0].networks and not engines[0].volumes
