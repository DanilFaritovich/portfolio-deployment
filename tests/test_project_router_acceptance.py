"""Offline acceptance boundaries; no registry, Docker or workflow dispatch."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
import yaml

from scripts import project_router_acceptance as a
from scripts.compose import LABEL
from scripts.gateway import desired_routes
from scripts.planning_inputs import build_desired_state
from scripts.validate_registry import validate_registry

ROOT = Path(__file__).resolve().parents[1]
SESSION = '0123456789abcdef'


def environment():
    return {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux',
            'RUNNER_ARCH': 'X64', 'GITHUB_REPOSITORY': 'DanilFaritovich/portfolio-deployment',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/develop',
            'ACCEPTANCE_REF_PROTECTED': 'true', 'ACCEPTANCE_CONFIRM': 'project-router-v1.0.0-isolated',
            'GITHUB_SHA': 'a' * 40}


def test_inactive_candidate_and_projection(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('candidate projection contacted an external process/network')
    monkeypatch.setattr(a.subprocess, 'run', forbidden)
    monkeypatch.setattr(a.urllib.request.OpenerDirector, 'open', forbidden)
    artifact = a.candidate_model(tmp_path, 'a' * 40, SESSION)
    desired = build_desired_state(tmp_path, 'a' * 40)
    assert desired_routes(desired) == ()
    assert validate_registry(ROOT).applications == ()
    assert desired.applications[0].spec['enabled'] is False
    web = artifact.model['services']['web']
    assert web['image'] == a.IMAGE
    assert web['user'] == '101:101' and web['read_only'] is True
    assert web['cap_drop'] == ['ALL'] and web['security_opt'] == ['no-new-privileges:true']
    assert web['restart'] == 'unless-stopped'
    assert web['tmpfs'] == ['/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777']
    assert not {'ports', 'build', 'volumes', 'secrets', 'environment', 'healthcheck'}.intersection(web)
    assert all(n['name'].startswith('pd-smoke-' + SESSION) for n in artifact.model['networks'].values())
    assert web['networks']['proxy']['aliases'] == [SESSION + '-project-router-web']


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


def test_candidate_rejects_valid_but_changed_security(tmp_path, monkeypatch):
    fake = tmp_path / 'source'
    (fake / 'docs/candidates').mkdir(parents=True)
    (fake / 'docs/templates').mkdir()
    (fake / 'policy').mkdir()
    for rel in ('docs/candidates/project-router.yaml', 'docs/templates/project-router.yaml', 'policy/admission.yaml'):
        (fake / rel).write_bytes((ROOT / rel).read_bytes())
    candidate = fake / 'docs/candidates/project-router.yaml'
    candidate.write_text(candidate.read_text().replace('readOnlyRootFilesystem: true', 'readOnlyRootFilesystem: false'))
    monkeypatch.setattr(a, 'ROOT', fake)
    with pytest.raises(a.AcceptanceError):
        a.candidate_model(tmp_path / 'registry', 'a' * 40, SESSION)


def origin_statement():
    return {'predicateType': a.ORIGIN_TYPE, 'subject': [{'name': a.REPOSITORY, 'digest': {'sha256': a.DIGEST[7:]}}],
            'predicate': {'sourceRepository': a.PRODUCER, 'sourceSha': a.SOURCE, 'sourceRef': 'refs/heads/main',
                          'platform': 'linux/amd64', 'buildArguments': {'VITE_SITE_ORIGIN': a.ORIGIN},
                          'originOutputVerified': True, 'runId': '39776962924', 'runAttempt': '1'}}


@pytest.mark.parametrize('field,value', [('sourceSha', 'a' * 40), ('sourceRef', 'refs/tags/v1.0.0'),
    ('sourceRepository', 'foreign/repo'), ('platform', 'linux/arm64'), ('originOutputVerified', False),
    ('runId', '1'), ('runAttempt', '2'), ('buildArguments', {'VITE_SITE_ORIGIN': 'https://wrong.test'})])
def test_origin_binding_rejects_mismatch(field, value):
    statement = origin_statement()
    a.verify_origin(statement)
    statement['predicate'][field] = value
    with pytest.raises(a.AcceptanceError):
        a.verify_origin(statement)


@pytest.mark.parametrize('predicate', [a.SLSA_TYPE, a.ORIGIN_TYPE])
def test_attestation_signature_command_and_subject(predicate):
    cmd = a.attestation_command(predicate, '/verified/gh')
    assert cmd[0] == '/verified/gh'
    assert cmd[cmd.index('--predicate-type') + 1] == predicate
    for flag, value in (('--source-digest', a.SOURCE), ('--signer-digest', a.SOURCE),
                        ('--source-ref', 'refs/heads/main'), ('--repo', a.PRODUCER),
                        ('--signer-workflow', 'github.com/' + a.PRODUCER + '/.github/workflows/publish.yml')):
        assert cmd[cmd.index(flag) + 1] == value
    assert '--deny-self-hosted-runners' in cmd
    assert 'oci://' + a.IMAGE in cmd
    statement = origin_statement()
    result = [{'verificationResult': {'statement': statement}}]
    assert a.verified_statement(json.dumps(result).encode(), a.ORIGIN_TYPE) == statement
    for bad in ([], result * 2, [{'verificationResult': {'statement': {}}}]):
        with pytest.raises(a.AcceptanceError):
            a.verified_statement(json.dumps(bad).encode(), a.ORIGIN_TYPE)
    statement['subject'][0]['digest']['sha256'] = 'a' * 64
    with pytest.raises(a.AcceptanceError):
        a.verified_statement(json.dumps(result).encode(), a.ORIGIN_TYPE)


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


def test_http_acceptance_fails_closed_and_bounds_paths(monkeypatch):
    monkeypatch.setattr(a, 'request', lambda ip, path: (200, {'Content-Type': 'text/html'}, b'<html>fallback</html>'))
    with pytest.raises(a.AcceptanceError):
        a.http_checks('172.18.0.2')


def test_no_redirect_credential_forwarding():
    req = a.urllib.request.Request('https://ghcr.io/blob', headers={'Authorization': 'Bearer private'})
    handler = a.RegistryRedirect()
    redirected = handler.redirect_request(req, None, 302, '', {}, 'https://pkg-containers.githubusercontent.com/blob')
    assert not redirected.has_header('Authorization')
    with pytest.raises(a.AcceptanceError):
        handler.redirect_request(req, None, 302, '', {}, 'http://evil.test/blob')


def test_cleanup_preserves_foreign_resources(tmp_path, monkeypatch):
    artifact = a.candidate_model(tmp_path / 'registry', 'a' * 40, SESSION)
    runtime = a.DisposableRuntime(artifact, tmp_path, SESSION)
    runtime.active = True
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'ps':
            return b'container-id'
        if args[:2] == ('container', 'inspect'):
            return json.dumps([{'Config': {'Labels': {LABEL + '.test-session': 'foreign'}}}]).encode()
        pytest.fail('attempted mutation after foreign identity')
    monkeypatch.setattr(runtime, 'docker', docker)
    with pytest.raises(a.AcceptanceError):
        runtime.cleanup()
    assert not any('rm' in call for call in calls)


def test_workflow_dispatch_only_and_least_privilege():
    # BaseLoader preserves the YAML `on` key as a string.
    workflow = yaml.load((ROOT / '.github/workflows/project-router-acceptance.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['acceptance']
    assert job['runs-on'] == 'ubuntu-24.04' and int(job['timeout-minutes']) <= 15
    assert 'github.ref_protected' in job['if'] and 'refs/heads/develop' in job['if']
    assert 'environment' not in job
    steps = job['steps']
    assert any(s.get('run') == 'python -m scripts.project_router_acceptance' for s in steps)
    artifact = steps[-1]
    assert artifact['if'] == 'always()' and artifact['with']['path'] == 'acceptance-evidence/report.json'
    for step in steps:
        if 'uses' in step:
            assert len(step['uses'].split('@')[1]) == 40
    text = (ROOT / '.github/workflows/project-router-acceptance.yml').read_text()
    assert 'secrets.' not in text and 'id-token:' not in text and 'packages:' not in text


def test_http_success_checks_assets_and_pdf_bytes(monkeypatch):
    calls = []
    def request(ip, path):
        calls.append(path)
        if path in ('/en/', '/ru/'):
            locale = path.strip('/')
            body = ('<html lang="' + locale + '"><link href="' + a.ORIGIN + path + '">'
                    '<script src="/assets/app.js"></script><link href="/assets/app.css"></html>').encode()
            return 200, {'Content-Type': 'text/html'}, body
        if path.endswith('.js'):
            return 200, {'Content-Type': 'application/javascript'}, b'console.log(1)'
        if path.endswith('.css'):
            return 200, {'Content-Type': 'text/css'}, b'body{}'
        if path.endswith('.pdf'):
            return 200, {'Content-Type': 'application/pdf'}, b'%PDF-1.7'
        if path.endswith('.png'):
            return 200, {'Content-Type': 'image/png'}, b'\x89PNG\r\n\x1a\n'
        if path == '/':
            return 302, {'Location': '/en/'}, b''
        if 'nonexistent' in path:
            return 404, {}, b'<html>Not found</html>'
        return 200, {}, a.ORIGIN.encode()
    monkeypatch.setattr(a, 'request', request)
    records = a.http_checks('172.18.0.2')
    assert len(records) == 13
    assert '/resumes/backend-ru.pdf' in calls and '/manager-acceptance-nonexistent' in calls
    assert all(set(r) == {'path', 'status', 'sha256', 'bytes'} for r in records)


@pytest.mark.parametrize('failed_predicate', [a.SLSA_TYPE, a.ORIGIN_TYPE])
def test_failed_signature_cannot_pull_or_start(tmp_path, monkeypatch, failed_predicate):
    for k, v in environment().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    monkeypatch.setattr(a, 'provision_gh', lambda directory: '/verified/gh')
    monkeypatch.setattr(a, 'candidate_model', lambda *args: type('Artifact', (), {'sha256': 'a' * 64})())
    evidence = type('Evidence', (), {'manifest_digest': a.DIGEST, 'image_id': 'sha256:' + 'b' * 64})()
    monkeypatch.setattr(a, 'registry_blobs', lambda: ({a.DIGEST: b'{}'}, evidence))
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        assert args[:3] == ['/verified/gh', 'attestation', 'verify']
        predicate = args[args.index('--predicate-type') + 1]
        if predicate != failed_predicate:
            statement = origin_statement()
            statement['predicateType'] = predicate
            return json.dumps([{'verificationResult': {'statement': statement}}]).encode()
        raise a.AcceptanceError('private diagnostic must not appear')
    monkeypatch.setattr(a, 'command', command)
    assert a.main() == 1
    report = json.loads((tmp_path / 'acceptance-evidence/report.json').read_text())
    assert report['result'] == 'failed' and report['stage'] == 'signed-attestations'
    assert report['productionAdmission'] is False
    assert 'private diagnostic' not in json.dumps(report)
    assert len(calls) == (1 if failed_predicate == a.SLSA_TYPE else 2)


@pytest.mark.parametrize('failure', ['checksum', 'version', 'download'])
def test_gh_failure_blocks_attestations_registry_and_docker(tmp_path, monkeypatch, failure):
    for k, v in environment().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    raw = gh_archive()
    monkeypatch.setattr(a, 'GH_ARCHIVE_SHA256', hashlib.sha256(raw).hexdigest())
    def forbidden(*args, **kwargs):
        pytest.fail('CLI integrity failure must precede candidate/registry/Docker access')
    monkeypatch.setattr(a, 'registry_blobs', forbidden)
    monkeypatch.setattr(a, 'candidate_model', forbidden)
    def command(args, **kwargs):
        if args[0] == 'curl':
            if failure == 'download':
                raise OSError('private download diagnostic')
            return b'corrupt' if failure == 'checksum' else raw
        assert args[1:] == ['--version'] and not kwargs.get('gh')
        return b'gh version 2.101.0 (2026-09-01)'
    monkeypatch.setattr(a, 'command', command)
    assert a.main() == 1
    report = json.loads((tmp_path / 'acceptance-evidence/report.json').read_text())
    assert report['stage'] == 'github-cli-integrity' and report['result'] == 'failed'
    assert 'attestations' not in report and report['productionAdmission'] is False
    assert 'private download diagnostic' not in json.dumps(report)


def test_gate_failure_preserves_preexisting_evidence(tmp_path, monkeypatch):
    output = tmp_path / 'acceptance-evidence'
    output.mkdir()
    previous = output / 'report.json'
    previous.write_text('existing evidence')
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert a.main() == 1
    assert previous.read_text() == 'existing evidence'
