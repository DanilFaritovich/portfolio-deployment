"""Offline synthetic producer/consumer integration. No published image is claimed."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from scripts import caddy_producer as p
from scripts import caddy_producer_acceptance as a
from scripts.caddy_producer_build_check import check
from scripts.caddy_producer_receipt import receipt
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import blob_digest
from scripts.production_integration.caddy_release import CaddyReleaseCollector
from scripts.production_integration.release import PRODUCER
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GHCRRead
from scripts.production_integration.runtime import CaddyRuntime
from scripts.runtime_integration import IntegrationFailure
from test_production_integration import paths
from test_release_collector import fixture as release_fixture


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline producer test attempted real network/process')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def test_lock_and_upstream_constraints():
    lock = p.load_lock()
    args = p.upstream_command(Path('/trusted/cosign'), Path('/inputs'), lock)
    for flag, value in (
        ('--certificate-identity', p.UPSTREAM + '/.github/workflows/release.yml@refs/tags/v' + lock['version']),
        ('--certificate-github-workflow-sha', lock['sourceSha']),
        ('--certificate-github-workflow-repository', 'caddyserver/caddy'),
        ('--certificate-oidc-issuer', 'https://token.actions.githubusercontent.com'),
        ('--certificate-github-workflow-trigger', 'push')):
        assert args[args.index(flag) + 1] == value
    assert not any('ignore' in arg or 'insecure' in arg for arg in args)


def test_download_mismatch_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'read_public', lambda url: b'substitution')
    with pytest.raises(DockerError):
        p.checked_download('https://example.invalid', '0' * 64, tmp_path / 'artifact')
    assert not (tmp_path / 'artifact').exists()


@pytest.mark.parametrize('mode', ['symlink', 'duplicate', 'missing-license', 'wrong-binary', 'arm64', 'interpreter'])
def test_archive_rejects_unsafe_or_incompatible_input(tmp_path, mode):
    binary = bytearray(256)
    binary[:6] = b'\x7fELF\x02\x01'
    binary[18:20] = b'\x3e\x00' if mode != 'arm64' else b'\xb7\x00'
    binary[32:40] = (64).to_bytes(8, 'little')
    binary[54:56] = (56).to_bytes(2, 'little')
    binary[56:58] = (1).to_bytes(2, 'little')
    binary[64:68] = (3 if mode == 'interpreter' else 1).to_bytes(4, 'little')
    lock = p.load_lock()
    lock['binarySha256'] = blob_digest(bytes(binary))[7:] if mode != 'wrong-binary' else '0' * 64
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, raw in [('caddy', bytes(binary)), ('LICENSE', b'license')] + (
                [('caddy', bytes(binary))] if mode == 'duplicate' else []):
            if name == 'LICENSE' and mode == 'missing-license':
                continue
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            if name == 'caddy' and mode == 'symlink':
                member.type = tarfile.SYMTYPE
                member.linkname = '/tmp/other'
            archive.addfile(member, io.BytesIO(raw))
    with pytest.raises(DockerError):
        p.extract_binary(output.getvalue(), lock, tmp_path)


def consumer_fixture():
    candidate, old_docs, _, _, _, _ = release_fixture()
    sha = candidate.producer_sha
    # Adapt the shared authenticated source fixture to the dedicated producer.
    docs = {}
    for (repo, path), value in old_docs.items():
        if repo != PRODUCER:
            continue
        raw = json.dumps(value).replace(PRODUCER, p.REPOSITORY).replace(
            'Publish validated image / publish', 'Publish trusted Caddy / Publish').replace(
            '.github/workflows/publish.yml', p.WORKFLOW)
        docs[path] = json.loads(raw)
    docs['/branches/main/protection']['required_status_checks']['checks'][0]['context'] = 'Offline checks'
    run = docs['/actions/runs?head_sha=' + sha + '&per_page=100']['workflow_runs'][0]
    run['path'] = '.github/workflows/caddy-image.yml'
    offline_run = deepcopy(run)
    offline_run.update(id=101, check_suite_id=21, path='.github/workflows/offline.yml')
    offline_run.pop('referenced_workflows')
    runs = docs['/actions/runs?head_sha=' + sha + '&per_page=100']
    runs['workflow_runs'].append(offline_run)
    runs['total_count'] = 2
    checks = docs['/commits/' + sha + '/check-runs?filter=latest&per_page=100']['check_runs']
    checks[0].update(name='Offline checks', check_suite={'id': 21})
    defaults = json.loads((Path(__file__).parent / 'fixtures/caddy/config.oci').read_bytes())
    defaults['config']['User'] = '0:0'
    defaults['config']['ExposedPorts'] = {'80/tcp': {}, '443/tcp': {}}
    config = json.dumps(defaults).encode()
    manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': blob_digest(config),
                   'size': len(config)}, 'layers': []}).encode()
    blobs = {blob_digest(raw): raw for raw in (config, manifest)}
    reference = p.IMAGE + '@' + blob_digest(manifest)
    docs['/attestations/' + blob_digest(manifest) + '?per_page=100'] = {'attestations': [{'bundle': {'test': True}}]}
    class GitHub:
        def get(self, repo, path):
            assert repo == p.REPOSITORY
            return deepcopy(docs[path])
    statements = tuple({'_type': 'https://in-toto.io/Statement/v1', 'predicateType': kind,
        'subject': [{'name': p.IMAGE, 'digest': {'sha256': blob_digest(manifest)[7:]}}], 'predicate': predicate}
        for kind, predicate in ((p.SLSA, {'buildDefinition': {
            'buildType': 'https://actions.github.io/buildtypes/workflow/v1',
            'externalParameters': {'workflow': {'path': '.github/workflows/caddy-image.yml',
                'ref': 'refs/heads/main', 'repository': 'https://github.com/' + p.REPOSITORY}},
            'resolvedDependencies': [{'uri': 'git+https://github.com/' + p.REPOSITORY + '@refs/heads/main',
                                      'digest': {'gitCommit': sha}}]},
            'runDetails': {'builder': {'id': p.BUILDER}, 'metadata': {
                'invocationId': 'https://github.com/' + p.REPOSITORY + '/actions/runs/100/attempts/1'}}}),
                                (p.PREDICATE, p.published_inputs(p.load_lock(), sha, '100', '1'))))
    class Crypto:
        called = False
        def verify(self, subject, image, identity, expectations, bundles):
            self.called = True
            assert subject == manifest and image == reference and bundles == [{'test': True}]
            assert identity == {'signerWorkflow': p.WORKFLOW, 'sourceSha': sha, 'signerSha': sha,
                                'sourceRef': 'refs/heads/main', 'signerIdentity': p.BUILDER}
            assert expectations[1]['expectedPredicate'] == p.published_inputs(p.load_lock(), sha, '100', '1')
            return statements
    crypto = Crypto()
    collector = CaddyReleaseCollector(github=GitHub(), registry=SimpleNamespace(collect=lambda image: blobs),
        crypto=crypto, checks=(WorkflowCheck(p.REPOSITORY, 'Offline checks', 15368, '.github/workflows/offline.yml'),),
        publication_check=WorkflowCheck(p.REPOSITORY, 'Publish trusted Caddy / Publish', 15368,
                                        '.github/workflows/caddy-image.yml'))
    return collector, CaddyRuntime(reference, p.load_lock()['version'], paths()), sha, docs, blobs, statements


def test_authenticated_caddy_source_oci_crypto_composition():
    collector, runtime, sha, _, _, _ = consumer_fixture()
    evidence = collector.collect(runtime, sha)
    assert evidence.reference == runtime.image and collector.crypto.called


def offline_push(sha, **changes):
    return dict(id=101, run_attempt=1, path='.github/workflows/offline.yml', event='push',
                head_sha=sha, head_branch='main', repository={'full_name': p.REPOSITORY},
                head_repository={'full_name': p.REPOSITORY}, status='in_progress', conclusion=None) | changes


def test_publication_waits_for_exact_push_and_ignores_pr_success():
    sha = 'a' * 40
    snapshots = iter([[], [offline_push(sha, event='pull_request', status='completed', conclusion='success')],
                      [offline_push(sha)], [offline_push(sha, status='completed', conclusion='success')]])
    sleeps = []
    def get(repo, path):
        assert repo == p.REPOSITORY and path == '/actions/runs?head_sha=' + sha + '&per_page=100'
        runs = next(snapshots)
        return {'total_count': len(runs), 'workflow_runs': runs}
    p.wait_for_offline(lambda: SimpleNamespace(get=get), sha, sleep=sleeps.append)
    assert sleeps == [15, 15, 15]


@pytest.mark.parametrize('failure', ['failure', 'cancelled', 'skipped', 'timed_out', 'duplicate',
    'sha', 'branch', 'repository', 'attempt', 'run', 'denied', 'pagination', 'timeout'])
def test_publication_wait_fails_closed(failure):
    sha = 'a' * 40
    calls = []
    sleeps = []
    def get(repo, path):
        calls.append(path)
        if failure == 'denied':
            raise DockerError('production-release-api-denied-or-rate-limited')
        run = offline_push(sha)
        if failure in ('failure', 'cancelled', 'skipped', 'timed_out'):
            run.update(status='completed', conclusion=failure)
        if failure == 'sha': run['head_sha'] = 'b' * 40
        if failure == 'branch': run['head_branch'] = 'develop'
        if failure == 'repository': run['head_repository']['full_name'] = 'attacker/repo'
        if failure == 'attempt': run['run_attempt'] = len(calls)
        if failure == 'run': run['id'] += len(calls)
        runs = [run, deepcopy(run)] if failure == 'duplicate' else [run]
        return {'total_count': 100 if failure == 'pagination' else len(runs), 'workflow_runs': runs}
    with pytest.raises(DockerError):
        p.wait_for_offline(lambda: SimpleNamespace(get=get), sha, sleep=sleeps.append)
    assert len(calls) <= 40 and len(sleeps) <= 39
    if failure == 'timeout': assert len(calls) == 40 and sleeps == [15] * 39


def test_publication_guard_preserves_full_source_policy(monkeypatch):
    from scripts.production_integration import release_transport as transport
    _, _, sha, docs, _, _ = consumer_fixture()
    monkeypatch.setenv('GITHUB_REPOSITORY', p.REPOSITORY)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_REF_PROTECTED', 'true')
    monkeypatch.setenv('GITHUB_SHA', sha)
    monkeypatch.setenv('GH_TOKEN', 'independent_read_only_policy_token')
    def factory(token, _):
        assert token == 'independent_read_only_policy_token'
        return SimpleNamespace(get=lambda repo, path: deepcopy(docs[path]))
    monkeypatch.setattr(transport, 'GitHubRead', factory)
    monkeypatch.setattr(transport, 'HTTPSRead', lambda: None)
    p.publication_guard()
    docs['/branches/main/protection']['enforce_admins']['enabled'] = False
    with pytest.raises(DockerError):
        p.publication_guard()


@pytest.mark.parametrize('failure', ['sha', 'tag', 'repository', 'develop', 'rules', 'workflow', 'unsigned',
    'signature', 'builder', 'subject', 'inputs', 'source-substitution', 'missing', 'platform', 'command', 'bytes',
    'publication-run', 'publication-attempt', 'signed-source-sha', 'upstream-sha'])
def test_consumer_fails_closed(failure):
    collector, runtime, sha, docs, blobs, statements = consumer_fixture()
    if failure == 'sha': sha = 'b' * 39
    if failure == 'tag': runtime = replace(runtime, image=p.IMAGE + ':latest')
    if failure == 'repository': runtime = replace(runtime, image=runtime.image.replace('portfolio-caddy', 'project-router'))
    if failure == 'develop': docs['/actions/runs?head_sha=' + sha + '&per_page=100']['workflow_runs'][0]['head_branch'] = 'develop'
    if failure == 'rules': docs['/branches/main/protection']['enforce_admins']['enabled'] = False
    if failure == 'workflow': docs['/actions/runs?head_sha=' + sha + '&per_page=100']['workflow_runs'][0]['referenced_workflows'][0]['sha'] = 'f' * 40
    if failure == 'unsigned': docs['/attestations/' + runtime.image.split('@')[1] + '?per_page=100']['attestations'] = []
    if failure == 'signature':
        def reject(*args): raise DockerError('production-release-signature-unverified')
        collector.crypto.verify = reject
    if failure == 'builder': statements[0]['predicate']['runDetails']['builder']['id'] = 'https://attacker.invalid'
    if failure == 'subject': statements[0]['subject'][0]['digest']['sha256'] = '0' * 64
    if failure == 'inputs': statements[1]['predicate']['inputs']['baseIndex'] = 'sha256:' + '0' * 64
    if failure == 'source-substitution': statements[1]['predicate']['upstreamRepository'] = 'attacker/caddy'
    if failure == 'publication-run': statements[1]['predicate']['runId'] = '999'
    if failure == 'publication-attempt': statements[1]['predicate']['runAttempt'] = '2'
    if failure == 'signed-source-sha': statements[1]['predicate']['sourceSha'] = 'f' * 40
    if failure == 'upstream-sha': statements[1]['predicate']['inputs']['sourceSha'] = 'f' * 40
    if failure == 'missing': del statements[0]['predicate']['runDetails']
    if failure in ('platform', 'command'):
        config_digest = next(d for d, raw in blobs.items() if 'architecture' in json.loads(raw))
        config = json.loads(blobs[config_digest])
        if failure == 'platform': config['architecture'] = 'arm64'
        else: config['config']['Cmd'] = ['sh']
        raw = json.dumps(config).encode()
        new_digest = blob_digest(raw)
        blobs[new_digest] = raw
        manifest = json.loads(blobs[runtime.image.split('@')[1]])
        manifest['config'].update(digest=new_digest, size=len(raw))
        raw = json.dumps(manifest).encode()
        blobs[blob_digest(raw)] = raw
        runtime = replace(runtime, image=p.IMAGE + '@' + blob_digest(raw))
    if failure == 'bytes': blobs[runtime.image.split('@')[1]] += b' '
    with pytest.raises(DockerError): collector.collect(runtime, sha)


def test_registry_scope_stays_separate():
    reader = GHCRRead(None)
    with pytest.raises(DockerError): reader.collect(p.IMAGE + '@sha256:' + 'a' * 64)
    with pytest.raises(DockerError): GHCRRead(None, repository='ghcr.io/attacker/caddy')


@pytest.mark.parametrize('image', ['ghcr.io/attacker/caddy@sha256:' + 'a' * 64,
    p.IMAGE + ':latest', p.IMAGE + '@sha256:' + 'a' * 63, p.IMAGE + '@sha256:' + 'A' * 64])
def test_runtime_only_admits_exact_producer_digest(image):
    with pytest.raises((DockerError, ValueError)):
        CaddyRuntime(image, '2.11.7', paths()).render()


@pytest.mark.parametrize('key,value', [('Cmd', ['sh']), ('Entrypoint', ['sh']), ('User', '1000'),
    ('Env', []), ('WorkingDir', '/'), ('Volumes', {'/data': {}}), ('Healthcheck', {'Test': ['CMD', 'true']}),
    ('ExposedPorts', {})])
def test_build_config_rejects_contract_drift(key, value):
    _, runtime, _, _, blobs, _ = consumer_fixture()
    manifest = json.loads(blobs[runtime.image.split('@')[1]])
    defaults = json.loads(blobs[manifest['config']['digest']])
    document = [{'Id': 'sha256:' + 'd' * 64, 'Os': 'linux', 'Architecture': 'amd64', 'Config': defaults['config']}]
    check(document)
    document[0]['Config'][key] = value
    with pytest.raises(DockerError): check(document)


@pytest.mark.parametrize('change', ['event', 'ref', 'protection', 'repo', 'endpoint'])
def test_producer_guard_preserves_disposable_and_source_boundaries(change, monkeypatch):
    from test_caddy_integration import runner_env
    from scripts import caddy_integration
    monkeypatch.setattr(caddy_integration.os, 'geteuid', lambda: 0)
    env = {**runner_env(), 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true'}
    a.producer_guard('unix:///var/run/docker.sock', 'disposable-caddy-integration', env)
    changes = {'event': ('GITHUB_EVENT_NAME', 'pull_request'), 'ref': ('GITHUB_REF', 'refs/heads/develop'),
               'protection': ('GITHUB_REF_PROTECTED', 'false'), 'repo': ('GITHUB_REPOSITORY', 'attacker/repo')}
    if change in changes:
        name, value = changes[change]
        env[name] = value
    with pytest.raises(IntegrationFailure):
        a.producer_guard('unix:///tmp/docker.sock' if change == 'endpoint' else 'unix:///var/run/docker.sock',
                         'disposable-caddy-integration', env)


def test_receipt_requires_exact_digest_and_sha():
    sha = 'b' * 40
    line = 'sha-' + sha + ': digest: sha256:' + 'c' * 64 + ' size: 1234\n'
    assert receipt(line, sha)['image'] == p.IMAGE + '@sha256:' + 'c' * 64
    for raw, source in [(line, 'b' * 39), (line.replace('sha256:', 'sha512:'), sha),
                        (line.replace('sha-' + sha, 'latest'), sha), (line * 2, sha)]:
        with pytest.raises(DockerError): receipt(raw, source)


def test_crypto_repository_policy_is_explicit(tmp_path):
    with pytest.raises(DockerError):
        AttestationVerifier('/a', 'b', '/c', 'd', repository='attacker/caddy')
    assert AttestationVerifier('/a', 'b', '/c', 'd', repository=p.REPOSITORY).repository == p.REPOSITORY


@pytest.mark.parametrize('mode', ['success', 'bad-signature', 'binary', 'root'])
def test_caddy_crypto_reuses_pinned_gh_and_exact_identity(tmp_path, monkeypatch, mode):
    from scripts.production_integration import release_crypto as crypto_module
    from test_release_collector import installed_crypto
    original = installed_crypto(tmp_path)
    adapter = AttestationVerifier(original.binary, original.binary_hash, original.root, original.root_hash,
                                  repository=p.REPOSITORY)
    collector, runtime, sha, _, _, statements = consumer_fixture()
    if mode == 'binary': adapter.binary.write_bytes(b'replaced')
    if mode == 'root': adapter.root.write_bytes(b'replaced')
    def process(args, directory):
        if args[-1] == '--version': return b'gh version 2.102.0 (2026-09-30)\n'
        for flag, value in (('--repo', p.REPOSITORY), ('--signer-digest', sha), ('--source-digest', sha),
            ('--source-ref', 'refs/heads/main'), ('--cert-identity', p.BUILDER), ('--cert-oidc-issuer', 'https://token.actions.githubusercontent.com')):
            assert args[args.index(flag) + 1] == value
        assert '--deny-self-hosted-runners' in args and '--custom-trusted-root' in args
        assert '--signer-workflow' not in args
        if mode == 'bad-signature': raise DockerError('production-release-signature-unverified')
        kind = args[args.index('--predicate-type') + 1]
        selected = next(s for s in statements if s['predicateType'] == kind)
        return json.dumps([{'verificationResult': {'statement': selected}}]).encode()
    monkeypatch.setattr(crypto_module, 'bounded_process', process)
    collector.crypto = adapter
    if mode == 'success': assert collector.collect(runtime, sha).reference == runtime.image
    else:
        with pytest.raises(DockerError): collector.collect(runtime, sha)


@pytest.mark.parametrize('key,value', [('baseIndex', 'latest'), ('sourceSha', 'a' * 39),
    ('archiveSha256', 'A' * 64), ('githubTrustRootSha256', '0' * 64), ('extra', 'anything')])
def test_input_lock_rejects_incomplete_or_mutable_evidence(tmp_path, monkeypatch, key, value):
    for path in p.INPUTS.iterdir():
        if path.is_file(): (tmp_path / path.name).write_bytes(path.read_bytes())
    lock = p.load_lock()
    lock[key] = value
    (tmp_path / 'inputs.lock.json').write_text(json.dumps(lock))
    monkeypatch.setattr(p, 'INPUTS', tmp_path)
    with pytest.raises(DockerError): p.load_lock()


@pytest.mark.parametrize('failed_verifier', [1, 2])
def test_input_authentication_failure_never_populates_build_context(tmp_path, monkeypatch, failed_verifier):
    monkeypatch.setattr(p, 'checked_download', lambda *args: b'not-a-release')
    calls = []
    def verifier(args, **kwargs):
        calls.append(args)
        if len(calls) == failed_verifier:
            raise DockerError('caddy-input-signature-or-command-failed')
        return b'[]'
    monkeypatch.setattr(p, 'run', verifier)
    directory = tmp_path / 'fresh'
    # The tool is intentionally unexecuted; create only the file whose chmod
    # follows the mocked authenticated download.
    def download(url, digest, path):
        path.write_bytes(b'not-a-release')
        return b'not-a-release'
    monkeypatch.setattr(p, 'checked_download', download)
    with pytest.raises(DockerError): p.prepare(directory)
    assert len(calls) == failed_verifier and not (directory / 'context/caddy').exists()


@pytest.mark.parametrize('failure', ['runner', 'engine', 'buildx', 'compose'])
def test_builder_toolchain_drift_fails_closed(monkeypatch, failure):
    lock = p.load_lock()
    for key, value in dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_ARCH='X64',
                           RUNNER_OS='Linux', ImageVersion=lock['runnerImageVersion']).items():
        monkeypatch.setenv(key, value)
    def command(args):
        if args[1] == 'version':
            return json.dumps({'Client': {'Version': lock['dockerVersion']},
                'Server': {'Version': '0.0.0' if failure == 'engine' else lock['dockerVersion']}}).encode()
        version = lock['buildxVersion'] if args[1] == 'buildx' else lock['composeVersion']
        if args[1] == failure: version = '0.0.0'
        return ('github.com/docker/buildx v' + version if args[1] == 'buildx' else version).encode()
    monkeypatch.setattr(p, 'run', command)
    if failure == 'runner': monkeypatch.setenv('ImageVersion', 'different')
    with pytest.raises(DockerError): p.check_toolchain()


@pytest.mark.parametrize('url', ['https://127.0.0.1:80/', 'http://169.254.169.254/',
    'http://127.0.0.1:2375/', 'http://user:secret@127.0.0.1:80/', 'http://127.0.0.1:80/private'])
def test_namespace_probe_rejects_unexpected_targets(url):
    harness = a.ProducerHarness.__new__(a.ProducerHarness)
    with pytest.raises(IntegrationFailure): harness.http('unused', url)


def test_debug_probe_never_executes_arbitrary_shell():
    harness = a.ProducerHarness.__new__(a.ProducerHarness)
    with pytest.raises(IntegrationFailure): harness.execute('sh', '-ec', 'arbitrary command')


def test_private_caddy_registry_credentials_are_origin_and_scope_bound():
    _, runtime, _, _, blobs, _ = consumer_fixture()
    calls = []
    class Transport:
        def get(self, url, headers):
            calls.append((url, headers))
            if url.startswith('https://ghcr.io/token?'):
                assert 'scope=repository:danilfaritovich/portfolio-caddy:pull' in url
                assert headers['Authorization'].startswith('Basic ')
                return b'{"token":"scoped-pull"}'
            assert headers['Authorization'] == 'Bearer scoped-pull'
            return blobs[url.rsplit('/', 1)[1]]
    reader = GHCRRead(Transport(), repository=p.IMAGE, registry_auth=('maintainer', 'private_token'))
    assert reader.collect(runtime.image) == blobs
    assert all('private_token' not in str(headers) for _, headers in calls)
    with pytest.raises(DockerError): GHCRRead(None, registry_auth=('maintainer', 'private_token'))


def test_caddy_runtime_still_requires_all_hardening():
    _, runtime, _, _, _, _ = consumer_fixture()
    service = runtime.render().model['services']['caddy']
    assert service['cap_drop'] == ['ALL'] and service['cap_add'] == ['NET_BIND_SERVICE']
    assert service['read_only'] is True and service['security_opt'] == ['no-new-privileges:true']
    assert len(service['volumes']) == 4 and service['volumes'][0]['read_only'] is True
    assert service['ports'] == ['80:80/tcp', '443:443/tcp'] and service['networks'] == ['proxy']


@pytest.fixture
def toolchain(monkeypatch):
    lock = p.load_lock()
    for key, value in dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_ARCH='X64',
                           RUNNER_OS='Linux', ImageVersion=lock['runnerImageVersion']).items():
        monkeypatch.setenv(key, value)
    responses = {
        'version': json.dumps({'Client': {'Version': lock['dockerVersion']},
                               'Server': {'Version': lock['dockerVersion']}}).encode(),
        'buildx': ('github.com/docker/buildx v' + lock['buildxVersion'] + ' public-commit').encode(),
        'compose': ('v' + lock['composeVersion'] + '\n').encode(),
    }
    calls = []
    def command(args):
        calls.append(args[1])
        value = responses[args[1]]
        if isinstance(value, Exception): raise value
        return value
    monkeypatch.setattr(p, 'run', command)
    return responses, calls


def test_toolchain_success_reports_each_check(toolchain, capsys):
    p.check_toolchain()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == 8 and all(row['status'] == 'ok' for row in records)
    assert records[-3]['actual'] == dict(client=p.load_lock()['dockerVersion'], server=p.load_lock()['dockerVersion'])


@pytest.mark.parametrize('component', ['version', 'buildx', 'compose'])
@pytest.mark.parametrize('response', [b'', b'credential-secret\n::error::injected',
                                     DockerError('credential-secret'), TimeoutError('credential-secret')])
def test_toolchain_unavailable_is_redacted_and_collects_remaining_checks(toolchain, monkeypatch, capsys, component, response):
    responses, calls = toolchain
    responses[component] = response
    monkeypatch.setattr('sys.argv', ['caddy_producer', 'toolchain-check'])
    assert p.main() == 1
    output = capsys.readouterr().out
    assert 'credential-secret' not in output and '::error::' not in output
    assert calls == ['version', 'buildx', 'compose']
    assert 'caddy-builder-' + ('docker' if component == 'version' else component) in output


@pytest.mark.parametrize('field', ['GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_ARCH', 'RUNNER_OS', 'ImageVersion'])
def test_runner_failure_prevents_commands_and_redacts_env(toolchain, monkeypatch, capsys, field):
    monkeypatch.setenv(field, 'credential-secret\n::error::injected')
    with pytest.raises(DockerError, match='^caddy-builder-runner-drift$'): p.check_toolchain()
    assert toolchain[1] == []
    assert 'credential-secret' not in capsys.readouterr().out


@pytest.mark.parametrize('side', ['Client', 'Server'])
def test_docker_sides_are_checked_independently(toolchain, capsys, side):
    responses, _ = toolchain
    versions = json.loads(responses['version'])
    versions[side]['Version'] = '0.0.0'
    responses['version'] = json.dumps(versions).encode()
    with pytest.raises(DockerError, match='^caddy-builder-docker-drift$'): p.check_toolchain()
    output = capsys.readouterr().out
    assert '"' + side.lower() + '": "0.0.0"' in output
    assert '"check": "compose"' in output


def test_toolchain_cli_redacts_unexpected_exception(toolchain, monkeypatch, capsys):
    def fail(): raise ValueError('credential-secret')
    monkeypatch.setattr(p, 'check_toolchain', fail)
    monkeypatch.setattr('sys.argv', ['caddy_producer', 'toolchain-check'])
    assert p.main() == 1
    assert capsys.readouterr().out == 'caddy-producer-inputs-failed\n'
