"""Offline boundaries for the explicit real QA command; no live substitutes in CLI."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import caddy_release_qa as qa
from scripts.caddy_producer import BUILDER, REPOSITORY, LEGACY_PREDICATE
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


def test_historical_builder_lock_does_not_relax_current_production_contract():
    from scripts.caddy_producer import load_lock, published_inputs
    collector, runtime, sha, _, _, statements = consumer_fixture()
    historical = qa.historical_input_lock()
    assert historical['runnerImageVersion'] == '20261012.334.1'
    current = load_lock()
    assert historical != current
    assert {k: v for k, v in historical.items() if k != 'runnerImageVersion'} == current
    statements[1]['predicate'] = published_inputs(historical, sha, '100', '1')
    statements[1]['predicateType'] = LEGACY_PREDICATE
    # The production collector still expects the current lock and rejects old inputs.
    with pytest.raises(DockerError, match='caddy-release-signed-subject-mismatch'):
        collector.collect(runtime, sha)
    def verify(subject, image, identity, expectations, bundles):
        assert expectations[1]['expectedPredicate'] == statements[1]['predicate']
        return statements
    evidence = verify_caddy_artifact(runtime, sha, ('100', '1'), github=collector.github,
        registry=collector.registry, crypto=SimpleNamespace(verify=verify), input_lock=historical)
    assert evidence.reference == runtime.image
    # Even historical QA requires exact signed lock equality.
    changed = deepcopy(historical)
    changed['runnerImageVersion'] = '20000101.1.1'
    def signatures(subject, image, identity, expectations, bundles): return statements
    with pytest.raises(DockerError, match='caddy-release-inputs-mismatch'):
        verify_caddy_artifact(runtime, sha, ('100', '1'), github=collector.github,
            registry=collector.registry, crypto=SimpleNamespace(verify=signatures), input_lock=changed)


def test_historical_qa_lock_bytes_are_pinned(monkeypatch):
    monkeypatch.setattr(Path, 'read_bytes', lambda path: b'{"runnerImageVersion":"unreviewed"}')
    with pytest.raises(DockerError, match='caddy-release-inputs-mismatch'): qa.historical_input_lock()


def test_legacy_release_policy_requires_exact_immutable_identity(monkeypatch):
    from scripts.production_integration import caddy_release as release
    from scripts.caddy_producer import load_lock
    source, image, publication = release.HISTORICAL_RELEASE
    runtime = SimpleNamespace(image=image)
    assert release.artifact_input_policy(runtime, source, publication) == qa.historical_input_lock()
    current = load_lock()
    monkeypatch.setattr(release, 'load_lock', lambda: {**current, 'dockerVersion': '99.0.0'})
    assert release.artifact_input_policy(runtime, source, publication) == qa.historical_input_lock()
    for sha, reference, run in ((source + '0', image, publication),
                                (source, image + '0', publication),
                                (source, image, (publication[0], '2'))):
        assert 'runnerImageVersion' not in release.artifact_input_policy(SimpleNamespace(image=reference), sha, run)


@pytest.mark.parametrize('field', ['schema', 'imageVersion', 'backend', 'environmentRedirects', 'extra', 'legacy'])
def test_v2_policy_signed_substitutions_fail(field):
    collector, runtime, sha, _, _, statements = consumer_fixture()
    predicate = statements[1]['predicate']
    if field == 'schema': predicate['schema'] = 'portfolio.caddy-inputs/v1'
    elif field == 'legacy': predicate['inputs']['runnerImageVersion'] = '20261012.334.1'
    elif field == 'extra': predicate['builderPolicy']['extra'] = True
    else: predicate['builderPolicy'][field] = 'attacker-policy'
    with pytest.raises(DockerError, match='caddy-release-inputs-mismatch'): collector.collect(runtime, sha)


@pytest.mark.parametrize('workflow,job', [('caddy-image.yml', 'build'), ('caddy-publish.yml', 'publish')])
def test_locked_buildx_provisioned_before_toolchain_check(workflow, job):
    lock = json.loads((ROOT / 'images/caddy/inputs.lock.json').read_text())
    steps = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())['jobs'][job]['steps']
    setup = next(s for s in steps if s.get('uses', '').startswith('docker/setup-buildx-action@'))
    assert setup['uses'] == 'docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069'
    assert setup['with'] == {'version': 'v' + lock['buildxVersion'], 'driver': 'docker',
                             'name': 'default', 'cleanup': False}
    check = next(s for s in steps if s.get('run') == 'python -m scripts.caddy_producer toolchain-check')
    assert steps.index(setup) < steps.index(check)
    assert not any(s.get('if') or s.get('continue-on-error') for s in (setup, check))
