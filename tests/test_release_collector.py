"""Controlled authenticated API/crypto-port fixtures; never live GitHub or Docker."""
import base64
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import urllib.error

import pytest

from scripts import application_acceptance as acceptance
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import SourceRequest, blob_digest
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.release import PRODUCER, MANAGER, ORIGIN, ReleaseVerifier, ReleaseFacts
from scripts.production_integration.release_collector import AuthenticatedReleaseCollector, SLSA, BUILD_ORIGIN
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GitHubRead, GHCRRead, HTTPSRead, NoRedirect
from scripts.reconciliation import encode

ROOT = Path(__file__).parent


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('unexpected network or process')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def fixture():
    config = encode({'os': 'linux', 'architecture': 'amd64', 'config': {}}).encode()
    manifest = encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': blob_digest(config),
                   'size': len(config)}, 'layers': []}).encode()
    index = encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
        'manifests': [{'platform': {'os': 'linux', 'architecture': 'amd64'},
        'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': blob_digest(manifest),
        'size': len(manifest)}]}).encode()
    blobs = {blob_digest(v): v for v in (config, manifest, index)}
    image = 'ghcr.io/danilfaritovich/project-router@' + blob_digest(index)
    c = OperationCandidate('a'*32, 'daemon', 'b'*40, 'c'*40, image,
        blob_digest(b'manifest'), blob_digest(b'policy'), *['sha256:'+'e'*64]*6, 'first-project-router-rollout')
    docs = {}
    for repo, sha in ((PRODUCER, c.producer_sha), (MANAGER, c.manager_sha)):
        metadata = {'full_name': repo, 'id': 42 if repo == PRODUCER else 43}
        docs[repo, ''] = metadata
        docs[repo, '/branches/main'] = {'name': 'main', 'protected': True, 'commit': {'sha': sha}}
        docs[repo, '/compare/'+sha+'...'+sha] = {'status': 'identical', 'merge_base_commit': {'sha': sha},
                                                    'base_commit': {'sha': sha}}
        docs[repo, '/rules/branches/main?per_page=100'] = []
        docs[repo, '/branches/main/protection'] = json.loads((ROOT/'fixtures/release/protection.json').read_text())
        run = {'id': 100, 'run_attempt': 1, 'head_sha': sha, 'head_branch': 'main', 'path': '.github/workflows/ci.yml',
               'event': 'push', 'status': 'completed', 'conclusion': 'success', 'repository': metadata,
               'head_repository': metadata, 'check_suite_id': 20}
        runs = [run]
        if repo == PRODUCER:
            run['referenced_workflows'] = [{'path': repo+'/.github/workflows/publish.yml@'+sha,
                                           'sha': sha, 'ref': 'refs/heads/main'}]
        docs[repo, '/actions/runs?head_sha='+sha+'&per_page=100'] = {'total_count': len(runs), 'workflow_runs': runs}
        docs[repo, '/commits/'+sha+'/check-runs?filter=latest&per_page=100'] = {'total_count': 1, 'check_runs': [
            {'name': 'ci', 'head_sha': sha, 'status': 'completed', 'conclusion': 'success',
             'app': {'id': 15368}, 'check_suite': {'id': 20}}]}
    publication = {'id': 200, 'name': 'Publish validated image / publish', 'head_sha': c.producer_sha,
                   'status': 'completed', 'conclusion': 'success', 'app': {'id': 15368},
                   'check_suite': {'id': 20},
                   'details_url': 'https://github.com/'+PRODUCER+'/actions/runs/100/job/200'}
    checks_doc = docs[PRODUCER, '/commits/'+c.producer_sha+'/check-runs?filter=latest&per_page=100']
    checks_doc['check_runs'].append(publication)
    checks_doc['total_count'] = 2
    docs[PRODUCER, '/actions/runs/100/attempts/1/jobs?per_page=100'] = {'total_count': 1, 'jobs': [
        {'id': 200, 'name': publication['name'], 'run_id': 100, 'run_attempt': 1,
         'head_sha': c.producer_sha, 'status': 'completed', 'conclusion': 'success',
         'check_run_url': 'https://api.github.com/repos/'+PRODUCER+'/check-runs/200'}]}
    for path, raw in (('registry/applications/project-router.yaml', b'manifest'), ('policy/admission.yaml', b'policy')):
        docs[MANAGER, '/contents/'+path+'?ref='+c.manager_sha] = {'type': 'file', 'path': path,
                'encoding': 'base64', 'size': len(raw), 'content': base64.b64encode(raw).decode()}
    docs[PRODUCER, '/attestations/'+image.split('@')[1]+'?per_page=100'] = {'attestations': [{'bundle': {'synthetic': True}}]}
    class Transport:
        def __init__(self): self.calls = []
        def get(self, url, headers):
            self.calls.append((url, dict(headers)))
            prefix = 'https://api.github.com/repos/'
            if url.startswith(prefix):
                assert headers['Authorization'] == 'Bearer test_read_only'
                suffix = url[len(prefix):]
                repo = next(r for r in (PRODUCER, MANAGER) if suffix == r or suffix.startswith(r+'/'))
                value = docs[repo, suffix[len(repo):]]
                if isinstance(value, Exception): raise value
                return encode(value).encode()
            assert 'test_read_only' not in str(headers)
            if url.startswith('https://ghcr.io/token?'):
                assert not headers
                return b'{"token":"anonymous-pull"}'
            digest = url.rsplit('/', 1)[1]
            return blobs[digest]
    transport = Transport()
    github = GitHubRead('test_read_only', transport)
    builder = 'https://github.com/'+PRODUCER+'/.github/workflows/publish.yml@'+c.producer_sha
    class Crypto:
        called = False
        def verify(self, subject, image_arg, identity, expectations, bundles):
            self.called = True
            assert blob_digest(subject) == image.split('@')[1] and image_arg == image
            assert identity['sourceSha'] == c.producer_sha and identity['signerSha'] == c.producer_sha
            self.expectations = expectations
            return ({'predicateType': SLSA, 'predicate': {'runDetails': {'builder': {'id': 'https://github.com/actions/runner/github-hosted'}}}},
                    {'predicateType': BUILD_ORIGIN, 'predicate': expectations[1]['expectedPredicate']})
    crypto = Crypto()
    checks = tuple(WorkflowCheck(r, 'ci', 15368, '.github/workflows/ci.yml') for r in (PRODUCER, MANAGER))
    collector = AuthenticatedReleaseCollector(c, github=github, registry=GHCRRead(transport), crypto=crypto,
                checks=checks, signer_sha=c.producer_sha, builder=builder,
                slsa_builder='https://github.com/actions/runner/github-hosted',
                publication_check=WorkflowCheck(PRODUCER, 'Publish validated image / publish', 15368, '.github/workflows/ci.yml'))
    verifier = ReleaseVerifier(c, required_checks=[(p.repository, p.name, p.app_id) for p in checks],
                               allowed_builders=[builder], collector=collector)
    request = SourceRequest(PRODUCER, c.producer_sha, c.manager_sha, image, ORIGIN)
    return c, docs, transport, crypto, verifier, request


def test_authenticated_round_through_release_verifier():
    c, docs, transport, crypto, verifier, request = fixture()
    assert not verifier.verify(request).blockers
    assert crypto.called
    assert crypto.expectations[1]['expectedPredicate']['runId'] == '100'
    assert len(transport.calls) < 64


@pytest.mark.parametrize('failure', [
    'ancestry', 'protected-flag-only', 'no-protection', 'checks-policy', 'unbound-app', 'admins', 'force', 'bypass',
    'check-app', 'check-sha', 'check-status', 'check-workflow', 'check-event', 'check-repository',
    'publish-event', 'publish-workflow', 'publish-sha', 'rate-limit', 'timeout', 'incomplete',
    'unknown-rules', 'partial-checks', 'partial-runs', 'content', 'unsigned', 'bad-signature', 'wrong-digest',
    'builder', 'moving-main',
])
def test_collection_fails_closed(failure):
    c, docs, transport, crypto, verifier, request = fixture()
    p = docs[PRODUCER, '/branches/main/protection']
    runs = docs[PRODUCER, '/actions/runs?head_sha='+c.producer_sha+'&per_page=100']
    checks = docs[PRODUCER, '/commits/'+c.producer_sha+'/check-runs?filter=latest&per_page=100']
    if failure == 'ancestry': docs[PRODUCER, '/compare/'+c.producer_sha+'...'+c.producer_sha]['status'] = 'diverged'
    if failure == 'protected-flag-only': docs[PRODUCER, '/branches/main/protection'] = {}
    if failure == 'no-protection': docs[PRODUCER, '/branches/main/protection'] = DockerError('production-release-api-not-found')
    if failure == 'checks-policy': p['required_status_checks']['checks'][0]['context'] = 'other'
    if failure == 'unbound-app': p['required_status_checks']['checks'][0]['app_id'] = -1
    if failure == 'admins': p['enforce_admins']['enabled'] = False
    if failure == 'force': p['allow_force_pushes']['enabled'] = True
    if failure == 'bypass': p['required_pull_request_reviews']['bypass_pull_request_allowances']['users'] = ['admin']
    if failure == 'check-app': checks['check_runs'][0]['app']['id'] = 42
    if failure == 'check-sha': checks['check_runs'][0]['head_sha'] = '0'*40
    if failure == 'check-status': checks['check_runs'][0]['conclusion'] = 'neutral'
    if failure == 'check-workflow': runs['workflow_runs'][0]['path'] = '.github/workflows/evil.yml'
    if failure == 'check-event': runs['workflow_runs'][0]['event'] = 'pull_request'
    if failure == 'check-repository': runs['workflow_runs'][0]['repository'] = {'id': 42, 'full_name': 'evil/repo'}
    if failure == 'publish-event': runs['workflow_runs'][0]['event'] = 'workflow_dispatch'
    if failure == 'publish-workflow': runs['workflow_runs'][0]['path'] = '.github/workflows/other.yml'
    if failure == 'publish-sha': runs['workflow_runs'][0]['head_sha'] = '0'*40
    if failure in ('rate-limit', 'timeout', 'unknown-rules'):
        docs[PRODUCER, '/rules/branches/main?per_page=100'] = DockerError('production-release-api-'+failure)
    if failure == 'incomplete': del checks['check_runs'][0]['app']
    if failure == 'partial-checks': checks['total_count'] = 3
    if failure == 'partial-runs': runs['total_count'] = 3
    if failure == 'content': docs[MANAGER, '/contents/policy/admission.yaml?ref='+c.manager_sha]['content'] = 'ZXZpbA=='
    if failure in ('unsigned', 'bad-signature'):
        def reject(*args): raise DockerError('production-release-signature-unverified')
        crypto.verify = reject
    if failure == 'wrong-digest': verifier.collector.registry.collect = lambda image: {image.split('@')[1]: b'evil'}
    if failure == 'builder': verifier.collector.slsa_builder = 'https://github.com/evil/builder'
    if failure == 'moving-main':
        original = verifier.collector.github.get
        count = 0
        def changed(repo, path):
            nonlocal count
            doc = original(repo, path)
            if repo == PRODUCER and path == '/branches/main':
                count += 1
                if count > 1: doc['commit']['sha'] = '0'*40
            return doc
        verifier.collector.github.get = changed
    with pytest.raises(DockerError, match='^(production-release|artifact-)'):
        verifier.verify(request)


@pytest.mark.parametrize('change', ['sourceSha', 'buildArguments', 'runId', 'runAttempt', 'originOutputVerified', 'subject', 'predicate'])
def test_signed_selector_rejects_substitutions(change):
    c, docs, transport, crypto, verifier, request = fixture()
    verifier.verify(request)
    expectation = crypto.expectations[1]
    statement = {'predicateType': BUILD_ORIGIN, 'subject': [{'name': request.reference.split('@')[0],
                 'digest': {'sha256': request.reference.split(':')[-1]}}],
                 'predicate': copy.deepcopy(expectation['expectedPredicate'])}
    if change == 'sourceSha': statement['predicate']['sourceSha'] = '0'*40
    if change == 'buildArguments': statement['predicate']['buildArguments']['VITE_SITE_ORIGIN'] = 'https://evil.test'
    if change == 'runId': statement['predicate']['runId'] = '666'
    if change == 'runAttempt': statement['predicate']['runAttempt'] = '2'
    if change == 'originOutputVerified': statement['predicate']['originOutputVerified'] = False
    if change == 'subject': statement['subject'][0]['digest']['sha256'] = '0'*64
    if change == 'predicate': statement['predicateType'] = SLSA
    raw = encode([{'verificationResult': {'statement': statement}}]).encode()
    with pytest.raises(acceptance.AcceptanceError): acceptance.verified_statement(raw, c.image, expectation)


@pytest.mark.parametrize('mode', ['valid', 'no-classic', 'bypass', 'disabled', 'unavailable', 'app', 'review', 'unenforced-checks'])
def test_effective_ruleset_applicability_and_details(mode):
    c, docs, transport, crypto, verifier, request = fixture()
    rules = [{'type': kind, 'ruleset_id': 7} for kind in ('pull_request', 'non_fast_forward', 'deletion', 'required_status_checks')]
    rules[0]['parameters'] = {'required_approving_review_count': 1, 'dismiss_stale_reviews_on_push': True}
    rules[3]['parameters'] = {'strict_required_status_checks_policy': True,
                             'required_status_checks': [{'context': 'ci', 'integration_id': 15368}]}
    docs[PRODUCER, '/rules/branches/main?per_page=100'] = rules
    detail = {'id': 7, 'target': 'branch', 'enforcement': 'active', 'bypass_actors': []}
    docs[PRODUCER, '/rulesets/7'] = detail
    if mode == 'no-classic': docs[PRODUCER, '/branches/main/protection'] = DockerError('production-release-api-not-found')
    if mode == 'bypass': detail['bypass_actors'] = [{'actor_id': 1}]
    if mode == 'disabled': detail['enforcement'] = 'evaluate'
    if mode == 'unavailable': docs[PRODUCER, '/rulesets/7'] = DockerError('production-release-api-denied-or-rate-limited')
    if mode == 'app': rules[3]['parameters']['required_status_checks'][0]['integration_id'] = -1
    if mode == 'review': rules[0]['parameters']['required_approving_review_count'] = 0
    if mode == 'unenforced-checks':
        rules.pop()
        docs[PRODUCER, '/branches/main/protection']['enforce_admins']['enabled'] = False
    if mode in ('valid', 'no-classic'): assert not verifier.verify(request).blockers
    else:
        with pytest.raises(DockerError): verifier.verify(request)


def installed_crypto(tmp_path):
    binary, root = tmp_path/'gh', tmp_path/'root'
    binary.write_bytes(b'pinned-verifier')
    root.write_bytes(b'pinned-root\n')
    return AttestationVerifier(binary, hashlib.sha256(binary.read_bytes()).hexdigest(),
                               root, hashlib.sha256(root.read_bytes()).hexdigest())


@pytest.mark.parametrize('mode', ['success', 'unsigned', 'invalid-signature', 'source', 'signer', 'origin', 'binary', 'root'])
def test_crypto_adapter_only_accepts_successful_pinned_verifier_output(tmp_path, monkeypatch, mode):
    from scripts.production_integration import release_crypto as module
    c, docs, transport, crypto, verifier, request = fixture()
    verifier.verify(request)
    adapter = installed_crypto(tmp_path)
    if mode == 'binary': adapter.binary.write_bytes(b'substituted')
    if mode == 'root': adapter.root.write_bytes(b'substituted')
    calls = []
    def process(args, directory):
        calls.append(args)
        if args[-1] == '--version': return b'gh version 2.102.0 (2026-09-30)\n'
        assert args[3] == str(directory/'subject')
        assert args[args.index('--bundle')+1] == str(directory/'bundles.jsonl')
        assert args[args.index('--custom-trusted-root')+1] == str(directory/'root.jsonl')
        assert args[args.index('--source-digest')+1] == c.producer_sha
        assert args[args.index('--signer-digest')+1] == c.producer_sha
        assert args[args.index('--source-ref')+1] == 'refs/heads/main'
        assert args[args.index('--signer-workflow')+1] == 'github.com/'+PRODUCER+'/.github/workflows/publish.yml'
        assert '--deny-self-hosted-runners' in args
        if mode in ('unsigned', 'invalid-signature', 'source', 'signer'):
            raise DockerError('production-release-signature-unverified')
        predicate = args[args.index('--predicate-type')+1]
        expectation = next(e for e in crypto.expectations if e['predicateType'] == predicate)
        statement = {'predicateType': predicate, 'predicate': copy.deepcopy(expectation.get('expectedPredicate', {})),
                     'subject': [{'name': c.image.split('@')[0], 'digest': {'sha256': c.image.split(':')[-1]}}]}
        if mode == 'origin' and predicate == BUILD_ORIGIN: statement['predicate']['buildArguments'] = {}
        return encode([{'verificationResult': {'statement': statement}}]).encode()
    monkeypatch.setattr(module, 'bounded_process', process)
    identity = {'signerWorkflow': '.github/workflows/publish.yml', 'signerSha': c.producer_sha,
                'sourceSha': c.producer_sha, 'sourceRef': 'refs/heads/main'}
    if mode == 'success':
        assert len(adapter.verify(b'subject', c.image, identity, crypto.expectations, [{}])) == 2
        assert len(calls) == 3
    else:
        with pytest.raises(DockerError): adapter.verify(b'subject', c.image, identity, crypto.expectations, [{}])
        if mode in ('binary', 'root'): assert not calls


def test_no_api_document_can_be_used_as_release_facts():
    c, docs, transport, crypto, verifier, request = fixture()
    verifier.collector.collect_authenticated = lambda *args: {'protected_main': True, 'verified': True}
    with pytest.raises(DockerError): verifier.verify(request)


@pytest.mark.parametrize('path', ['https://evil.test/', '//evil.test/', '/contents/../secret', '/contents/%2e%2e', '/x#fragment'])
def test_fixed_api_paths_reject_untrusted_urls(path):
    with pytest.raises(DockerError): GitHubRead('test_token', object()).get(PRODUCER, path)


@pytest.mark.parametrize('url', ['http://api.github.com/', 'https://evil.test/', 'https://api.github.com@evil.test/',
                                 'https://api.github.com:443/', 'https://ghcr.io/#fragment'])
def test_fixed_transport_hosts(url):
    with pytest.raises(DockerError): HTTPSRead().get(url, {'Authorization': 'Bearer secret'})


@pytest.mark.parametrize('failure', ['timeout', 'rate', 'denied', 'missing', 'partial', 'size', 'redirect', 'budget'])
def test_http_failures_are_bounded_and_redacted(failure):
    transport = HTTPSRead()
    class Response:
        status = 200
        headers = {'Link': '<https://evil.test/>; rel="next"'} if failure == 'partial' else {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read1(self, size): return b'x'*size if failure == 'size' else b''
    class Opener:
        def open(self, request, timeout):
            assert timeout <= 10 and request.get_method() == 'GET'
            if failure == 'timeout': raise TimeoutError('SECRET')
            if failure in ('rate', 'denied', 'missing', 'redirect'):
                code = {'rate': 429, 'denied': 403, 'missing': 404, 'redirect': 302}[failure]
                raise urllib.error.HTTPError(request.full_url, code, 'SECRET', {}, None)
            return Response()
    transport.opener = Opener()
    if failure == 'budget': transport.remaining = 0
    with pytest.raises(DockerError) as error:
        transport.get('https://api.github.com/repos/test', {})
    assert 'SECRET' not in str(error.value) and str(error.value).startswith('production-release-')
    assert NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/') is None


def test_caddy_unsigned_statement_cannot_enter_collector():
    c, docs, transport, crypto, verifier, request = fixture()
    lock = json.loads((ROOT/'fixtures/caddy/image-lock.json').read_text())
    statement = json.loads((ROOT/'fixtures/caddy/provenance.oci').read_text())
    assert statement['predicateType'] == 'https://slsa.dev/provenance/v0.2'
    assert lock['availableAttestation']['signatureVerified'] is False
    with pytest.raises(DockerError): verifier.verify(replace(request, reference='docker.io/library/caddy@'+lock['index']))


@pytest.mark.parametrize('target', ['https://pkg-containers.githubusercontent.com/blob?signature=private',
                                  'https://evil.test/blob', 'http://pkg-containers.githubusercontent.com/blob',
                                  'https://pkg-containers.githubusercontent.com.evil.test/blob'])
def test_ghcr_redirect_never_forwards_credentials(target):
    transport = HTTPSRead()
    calls = []
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read1(self, size): return b''
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 307, 'private', {'Location': target}, None)
            assert request.full_url == target and not request.has_header('Authorization')
            return Response()
    transport.opener = Opener()
    if target.startswith('https://pkg-containers.githubusercontent.com/'):
        assert transport.get('https://ghcr.io/v2/owner/repo/blobs/sha256:abc', {'Authorization': 'Bearer registry-secret'}) == b''
        assert len(calls) == 2
    else:
        with pytest.raises(DockerError): transport.get('https://ghcr.io/v2/owner/repo/blobs/sha256:abc', {})
        assert len(calls) == 1


@pytest.mark.parametrize('change', [
    'missing', 'duplicate-job', 'failed', 'skipped', 'cancelled', 'attempt', 'run', 'sha',
    'job-url', 'check-id', 'check-app', 'check-suite', 'check-url', 'check-failed',
    'reference-missing', 'reference-sha', 'reference-path', 'reference-ref', 'duplicate-run', 'partial-jobs',
])
def test_publication_requires_exact_reusable_job_and_check(change):
    c, docs, transport, crypto, verifier, request = fixture()
    jobs = docs[PRODUCER, '/actions/runs/100/attempts/1/jobs?per_page=100']
    job = jobs['jobs'][0]
    checks = docs[PRODUCER, '/commits/'+c.producer_sha+'/check-runs?filter=latest&per_page=100']
    check = checks['check_runs'][1]
    runs = docs[PRODUCER, '/actions/runs?head_sha='+c.producer_sha+'&per_page=100']
    run = runs['workflow_runs'][0]
    if change == 'missing': jobs.update(total_count=0, jobs=[])
    if change == 'duplicate-job': jobs['jobs'].append(copy.deepcopy(job)); jobs['total_count'] = 2
    if change in ('failed', 'skipped', 'cancelled'): job['conclusion'] = change
    if change == 'attempt': job['run_attempt'] = 2
    if change == 'run': job['run_id'] = 101
    if change == 'sha': job['head_sha'] = '0'*40
    if change == 'job-url': job['check_run_url'] = 'https://evil.test/check-runs/200'
    if change == 'check-id': check['id'] = 201
    if change == 'check-app': check['app']['id'] = 999
    if change == 'check-suite': check['check_suite']['id'] = 21
    if change == 'check-url': check['details_url'] = check['details_url'].replace('/100/', '/101/')
    if change == 'check-failed': check['conclusion'] = 'failure'
    if change == 'reference-missing': run['referenced_workflows'] = []
    if change == 'reference-sha': run['referenced_workflows'][0]['sha'] = '0'*40
    if change == 'reference-path': run['referenced_workflows'][0]['path'] = 'evil/repo/.github/workflows/publish.yml@'+c.producer_sha
    if change == 'reference-ref': run['referenced_workflows'][0]['ref'] = 'refs/heads/develop'
    if change == 'duplicate-run': runs['workflow_runs'].append(dict(run, id=101)); runs['total_count'] = 2
    if change == 'partial-jobs': jobs['total_count'] = 2
    with pytest.raises(DockerError): verifier.verify(request)
    assert not crypto.called


def test_real_api_reusable_structure_is_regression_data_only():
    """Use captured structure with synthetic source/candidate; never install real IDs as policy."""
    c, docs, transport, crypto, verifier, request = fixture()
    captured = json.loads((ROOT/'fixtures/release/reusable-publication.json').read_text())
    run = captured['run']
    source_sha = run['head_sha']
    # Rebind captured source SHA to this test's candidate. The live IDs stay
    # fixture evidence, not defaults inside the installation implementation.
    raw = json.dumps(captured).replace(source_sha, c.producer_sha)
    captured = json.loads(raw)
    run = captured['run']
    metadata = docs[PRODUCER, '']
    run.update(repository=metadata, head_repository=metadata)
    docs[PRODUCER, '/actions/runs?head_sha='+c.producer_sha+'&per_page=100'] = {'total_count': 1, 'workflow_runs': [run]}
    docs[PRODUCER, '/actions/runs/'+str(run['id'])+'/attempts/1/jobs?per_page=100'] = captured['jobs']
    docs[PRODUCER, '/commits/'+c.producer_sha+'/check-runs?filter=latest&per_page=100'] = captured['checks']
    docs[PRODUCER, '/branches/main/protection']['required_status_checks']['checks'][0]['context'] = 'Quality and static build'
    verifier.collector.checks = (WorkflowCheck(PRODUCER, 'Quality and static build', 15368, '.github/workflows/ci.yml'),
                                verifier.collector.checks[1])
    verifier.required_checks = frozenset((p.repository, p.name, p.app_id) for p in verifier.collector.checks)
    assert not verifier.verify(request).blockers
    assert crypto.expectations[1]['expectedPredicate']['runId'] == str(run['id'])
    assert crypto.expectations[1]['expectedPredicate']['runAttempt'] == str(run['run_attempt'])


def test_manager_develop_sha_blocks_before_artifacts_or_crypto():
    c, docs, transport, crypto, verifier, request = fixture()
    docs[MANAGER, '/compare/'+c.manager_sha+'...'+c.manager_sha]['status'] = 'behind'
    with pytest.raises(DockerError, match='^production-release-sha-not-on-main$'):
        verifier.verify(request)
    assert not crypto.called
    assert all('ghcr.io/' not in url and '/attestations/' not in url for url, _ in transport.calls)


def readiness_installation(tmp_path, monkeypatch):
    from scripts.production_integration import release_readiness as module
    c, docs, transport, crypto, verifier, request = fixture()
    pinned = installed_crypto(tmp_path)
    installation = module.ReleaseInstallation(c, verifier.collector.checks, verifier.collector.publication_check,
        c.producer_sha, verifier.collector.builder, verifier.collector.slsa_builder,
        pinned.binary, pinned.binary_hash, pinned.root, pinned.root_hash)
    monkeypatch.setattr(module, 'HTTPSRead', lambda: transport)
    monkeypatch.setattr(module, 'AttestationVerifier', lambda *args: crypto)
    return module, installation, docs, transport, crypto


@pytest.mark.parametrize('mode', ['no-opt-in', 'truthy-opt-in', 'json-installation', 'missing-pin', 'bad-root',
                                  'token', 'manager-main', 'denied', 'secret-error', 'success'])
def test_read_only_readiness_is_opt_in_redacted_and_never_authorizes(tmp_path, monkeypatch, mode):
    module, installation, docs, transport, crypto = readiness_installation(tmp_path, monkeypatch)
    opt_in = True
    token = 'test_read_only'
    if mode == 'no-opt-in': opt_in = False
    if mode == 'truthy-opt-in': opt_in = 1
    if mode == 'json-installation': installation = {'reviewed': True}
    if mode == 'missing-pin': installation = replace(installation, gh_sha256='')
    if mode == 'bad-root': installation.trusted_root.write_bytes(b'unreviewed')
    if mode == 'token': token = None
    if mode == 'manager-main':
        sha = installation.candidate.manager_sha
        docs[MANAGER, '/compare/'+sha+'...'+sha]['status'] = 'diverged'
    if mode == 'denied': docs[PRODUCER, ''] = DockerError('production-release-api-denied-or-rate-limited')
    if mode == 'secret-error': docs[PRODUCER, ''] = DockerError('token=secret response text https://private.test')
    report = module.check_release_readiness(installation, authorize_read_only=opt_in, github_token=token)
    assert report['productionAuthorized'] is False and report['readOnly'] is True
    assert report['releaseVerified'] is (mode == 'success')
    assert set(report) == {'productionAuthorized', 'readOnly', 'releaseVerified', 'blockers'}
    if mode != 'success': assert len(report['blockers']) == 1
    if mode == 'manager-main': assert report['blockers'] == ['production-release-sha-not-on-main']
    if mode == 'secret-error': assert report['blockers'] == ['production-release-readiness-unverified']
    assert all(secret not in encode(report) for secret in ('secret', 'test_read_only', 'private.test', 'daemon', 'sha256:'))
    if mode in ('no-opt-in', 'truthy-opt-in', 'json-installation', 'missing-pin', 'bad-root', 'token'):
        assert not transport.calls and not crypto.called


def test_readiness_uses_a_fresh_collection_and_never_saved_success(tmp_path, monkeypatch):
    module, installation, docs, transport, crypto = readiness_installation(tmp_path, monkeypatch)
    assert module.check_release_readiness(installation, authorize_read_only=True, github_token='test_read_only')['releaseVerified']
    docs[PRODUCER, ''] = DockerError('production-release-api-denied-or-rate-limited')
    report = module.check_release_readiness(installation, authorize_read_only=True, github_token='test_read_only')
    assert report['blockers'] == ['production-release-api-denied-or-rate-limited']
    assert report['productionAuthorized'] is False
