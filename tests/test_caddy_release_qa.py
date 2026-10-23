"""Offline boundaries for the explicit real QA command; no live substitutes in CLI."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import caddy_release_qa as qa
from scripts.caddy_producer import BUILDER, REPOSITORY
from scripts.docker_adapter import DockerError
from scripts.production_integration.caddy_release import verify_caddy_artifact
from test_caddy_producer import consumer_fixture

ROOT = Path(__file__).resolve().parents[1]


def test_qa_and_collector_share_artifact_contract_without_source_bypass(monkeypatch):
    collector, runtime, sha, docs, _, _ = consumer_fixture()
    def forbidden(*args, **kwargs):
        raise DockerError('privileged-source-required')
    monkeypatch.setattr('scripts.production_integration.caddy_release.ProtectedSource', forbidden)
    evidence = verify_caddy_artifact(runtime, sha, ('100', '1'), github=collector.github,
                                    registry=collector.registry, crypto=collector.crypto)
    assert evidence.reference == runtime.image
    with pytest.raises(DockerError, match='privileged-source-required'):
        collector.collect(runtime, sha)


@pytest.mark.parametrize('field', ['source', 'workflow', 'ref', 'repository', 'build-type', 'run', 'attempt',
                                  'old-builder', 'builder-sha', 'predicate-type', 'extra-input', 'subject-name'])
def test_signed_contract_substitutions_fail(field):
    collector, runtime, sha, _, _, statements = consumer_fixture()
    slsa = statements[0]['predicate']
    workflow = slsa['buildDefinition']['externalParameters']['workflow']
    if field == 'source': slsa['buildDefinition']['resolvedDependencies'][0]['digest']['gitCommit'] = 'b' * 40
    if field == 'workflow': workflow['path'] = '.github/workflows/attacker.yml'
    if field == 'ref': workflow['ref'] = 'refs/heads/develop'
    if field == 'repository': workflow['repository'] += '-attacker'
    if field == 'build-type': slsa['buildDefinition']['buildType'] += '-other'
    if field in ('run', 'attempt'):
        slsa['runDetails']['metadata']['invocationId'] = 'https://github.com/' + REPOSITORY + '/actions/runs/' + (
            '101/attempts/1' if field == 'run' else '100/attempts/2')
    if field == 'old-builder': slsa['runDetails']['builder']['id'] = 'https://github.com/actions/runner/github-hosted'
    if field == 'builder-sha': slsa['runDetails']['builder']['id'] = BUILDER.replace('refs/heads/main', sha)
    if field == 'predicate-type': statements[1]['predicateType'] += '-other'
    if field == 'extra-input': statements[1]['predicate']['unreviewed'] = True
    if field == 'subject-name': statements[1]['subject'][0]['name'] += '-attacker'
    with pytest.raises(DockerError): collector.collect(runtime, sha)


@pytest.mark.parametrize('mode', ['ok', 'missing', 'duplicate', 'other', 'wrong-path'])
def test_exact_historical_bundle_selection(mode):
    documents = [{'bundle_url': 'https://example.invalid/attestations/' + identity + '.json.sn?secret=never-follow',
                  'bundle': {'historical': identity}} for identity in sorted(qa.ATTESTATIONS)]
    if mode == 'missing': documents.pop()
    if mode == 'duplicate': documents.append(deepcopy(documents[0]))
    if mode == 'other': documents[0]['bundle_url'] = 'https://example.invalid/attestations/999.json.sn'
    github = qa.HistoricalAttestations(SimpleNamespace(get=lambda *args: {'attestations': documents}))
    if mode == 'ok':
        assert len(github.get(REPOSITORY, '/attestations/' + qa.DIGEST + '?per_page=100')['attestations']) == 2
    else:
        with pytest.raises(DockerError):
            github.get(REPOSITORY, '/attestations/' + qa.DIGEST + ('?per_page=1' if mode == 'wrong-path' else '?per_page=100'))


@pytest.mark.parametrize('error', [None, DockerError('production-release-signature-unverified'),
    DockerError('token=secret-value'), ValueError('private API response')])
def test_cli_fixed_summary_and_failure_exit(monkeypatch, capsys, error):
    def check():
        if error: raise error
    monkeypatch.setattr(qa, 'check_release', check)
    assert qa.main() == (1 if error else 0)
    output = capsys.readouterr().out
    assert output.startswith('FAIL' if error else 'PASS')
    assert qa.DIGEST in output and 'productionAuthorized=false' in output
    assert 'secret-value' not in output and 'private API response' not in output
    if error and 'signature-unverified' in str(error): assert 'code=production-release-signature-unverified' in output
    elif error: assert 'code=caddy-qa-failed' in output


def test_pr_workflow_read_only_and_full_ci_deduplicated():
    workflow = yaml.safe_load((ROOT / '.github/workflows/caddy-release-qa.yml').read_text())
    assert workflow[True] == {'pull_request': {'branches': ['develop', 'main']}}
    assert workflow['permissions'] == {'contents': 'read', 'attestations': 'read'}
    raw = json.dumps(workflow)
    assert 'secrets.' not in raw and 'write' not in raw and 'pull_request_target' not in raw
    job = workflow['jobs']['release-qa']
    assert not job.get('permissions')
    assert any(step.get('run') == 'make qa-release-check PYTHON=python' for step in job['steps'])
    checkout = next(s for s in job['steps'] if 'actions/checkout@' in s.get('uses', ''))
    assert checkout['with']['persist-credentials'] is False
    offline = (ROOT / '.github/workflows/offline.yml').read_text()
    producer = (ROOT / '.github/workflows/caddy-image.yml').read_text()
    assert offline.count('make check PYTHON=python') == 1
    assert 'make check' not in producer
    for operation in ('prepare', 'toolchain-check', 'scripts.caddy_producer_build_check'):
        assert operation in producer
    publisher = (ROOT / '.github/workflows/caddy-publish.yml').read_text()
    assert publisher.index('scripts.caddy_producer publish-guard') < publisher.index('Upload immutable candidate bytes')
    makefile = (ROOT / 'Makefile').read_text()
    assert 'qa-release-check' not in makefile.split('check:\n', 1)[1].split('\n\n', 1)[0]


@pytest.mark.parametrize('mode', ['deprecation', 'next', 'mixed', 'other', 'registry'])
def test_api_deprecation_link_never_hides_pagination(mode):
    from scripts.production_integration.release_transport import HTTPSRead
    notice = '<https://docs.github.com/en/rest/about-the-rest-api/api-versions>; rel="deprecation"; type="text/html"'
    links = {'deprecation': notice, 'registry': notice, 'next': '<https://api.github.com/next>; rel="next"',
             'mixed': notice + ', <https://api.github.com/next>; rel="next"',
             'other': notice.replace('docs.github.com', 'attacker.invalid')}
    class Response:
        status = 200
        headers = {'Link': links[mode]}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read1(self, size): return b''
    transport = HTTPSRead()
    transport.opener = SimpleNamespace(open=lambda *args, **kwargs: Response())
    url = 'https://ghcr.io/v2/test' if mode == 'registry' else 'https://api.github.com/repos/test'
    if mode == 'deprecation': assert transport.get(url, {}) == b''
    else:
        with pytest.raises(DockerError, match='pagination-unproven'): transport.get(url, {})
