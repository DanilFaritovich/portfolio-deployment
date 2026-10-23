"""Offline safety and release gating contracts for the separate live App smoke."""
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
import yaml

from scripts import caddy_source_smoke as smoke
from scripts.docker_adapter import DockerError

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
MAIN = 'b' * 40


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('unexpected network'))


def test_live_workflow_is_trusted_develop_only_and_read_only():
    workflow = yaml.safe_load((ROOT / '.github/workflows/caddy-source-smoke.yml').read_text())
    assert workflow[True] == {'push': {'branches': ['develop']}}
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['source']
    for gate in ("github.event_name == 'push'", "github.ref == 'refs/heads/develop'", 'github.ref_protected',
                 "github.repository == 'DanilFaritovich/portfolio-deployment'"):
        assert gate in job['if']
    steps = job['steps']
    issue = next(s for s in steps if s.get('id') == 'source-app')
    publisher = yaml.safe_load((ROOT / '.github/workflows/caddy-publish.yml').read_text())
    publish_issue = next(s for s in publisher['jobs']['publish']['steps'] if s.get('id') == 'source-app')
    assert {k: v for k, v in issue['with'].items() if k != 'private-key'} == {
        k: v for k, v in publish_issue['with'].items() if k != 'private-key'}
    assert issue['with']['skip-token-revoke'] is False
    users = [s for s in steps if 'steps.source-app.outputs.token' in json.dumps(s)]
    assert len(users) == 1 and users[0]['run'] == 'python -m scripts.caddy_source_smoke verify'
    for s in steps:
        assert not s.get('continue-on-error')
        assert 'docker' not in s.get('run', '').lower()
    assert 'packages' not in json.dumps(workflow) and 'id-token' not in json.dumps(workflow)


@pytest.mark.parametrize('failure', ['success', 'unprotected-context', 'pr', 'main', 'foreign-repo',
                                     'unprotected-api', 'develop-moved', 'source-denied'])
def test_smoke_checks_real_context_and_shares_full_source_verifier(monkeypatch, capsys, failure):
    env = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REPOSITORY': smoke.REPOSITORY,
           'GITHUB_REF': 'refs/heads/develop', 'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': SHA,
           'GH_TOKEN': 'synthetic_opaque.token'}
    if failure == 'unprotected-context': env['GITHUB_REF_PROTECTED'] = 'false'
    if failure == 'pr': env['GITHUB_EVENT_NAME'] = 'pull_request'
    if failure == 'main': env['GITHUB_REF'] = 'refs/heads/main'
    if failure == 'foreign-repo': env['GITHUB_REPOSITORY'] = 'attacker/repo'
    for k, v in env.items(): monkeypatch.setenv(k, v)
    calls, verified = [], []
    def get(repo, path):
        calls.append(path)
        if path == '/branches/develop':
            return {'name': 'develop', 'protected': failure != 'unprotected-api',
                    'commit': {'sha': MAIN if failure == 'develop-moved' else SHA}}
        assert path == '/branches/main'
        return {'commit': {'sha': MAIN}}
    monkeypatch.setattr(smoke, 'GitHubRead', lambda token, transport: SimpleNamespace(get=get))
    monkeypatch.setattr(smoke, 'HTTPSRead', lambda: None)
    def verify(factory, sha):
        assert sha == MAIN
        verified.append(sha)
        if failure == 'source-denied': raise DockerError('production-release-api-denied-or-rate-limited')
    monkeypatch.setattr(smoke, 'verify_main_source', verify)
    monkeypatch.setattr('sys.argv', ['caddy_source_smoke', 'verify'])
    assert smoke.main() == (0 if failure == 'success' else 1)
    out = capsys.readouterr()
    assert 'synthetic_opaque.token' not in out.out + out.err and not out.err
    assert bool(verified) == (failure in ('success', 'source-denied'))
    if failure in ('unprotected-context', 'pr', 'main', 'foreign-repo'): assert not calls


@pytest.mark.parametrize('change', ['success', 'missing', 'failed', 'pending', 'duplicate', 'sha', 'branch',
    'event', 'path', 'repo', 'head-repo', 'develop-moved', 'unprotected', 'partial', 'denied'])
def test_release_requires_successful_live_run_for_exact_develop_sha(monkeypatch, change):
    run = {'head_sha': SHA, 'head_branch': 'develop', 'event': 'push',
           'path': '.github/workflows/caddy-source-smoke.yml', 'status': 'completed', 'conclusion': 'success',
           'repository': {'full_name': smoke.REPOSITORY}, 'head_repository': {'full_name': smoke.REPOSITORY}}
    if change == 'failed': run['conclusion'] = 'failure'
    if change == 'pending': run.update(status='in_progress', conclusion=None)
    if change == 'sha': run['head_sha'] = MAIN
    if change == 'branch': run['head_branch'] = 'main'
    if change == 'event': run['event'] = 'pull_request'
    if change == 'path': run['path'] = '.github/workflows/offline.yml'
    if change == 'repo': run['repository']['full_name'] = 'attacker/repo'
    if change == 'head-repo': run['head_repository']['full_name'] = 'attacker/repo'
    runs = [] if change == 'missing' else [run, deepcopy(run)] if change == 'duplicate' else [run]
    def get(repo, path):
        if change == 'denied': raise DockerError('production-release-api-denied-or-rate-limited')
        if path == '/branches/develop':
            return {'protected': change != 'unprotected', 'commit': {'sha': MAIN if change == 'develop-moved' else SHA}}
        assert path == '/actions/workflows/caddy-source-smoke.yml/runs?head_sha=' + SHA + '&event=push&per_page=100'
        return {'total_count': len(runs) + (change == 'partial'), 'workflow_runs': runs}
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    monkeypatch.setenv('EXPECTED_DEVELOP_SHA', SHA)
    monkeypatch.setattr(smoke, 'GitHubRead', lambda *a: SimpleNamespace(get=get))
    monkeypatch.setattr(smoke, 'HTTPSRead', lambda: None)
    if change == 'success': smoke.require_success()
    else:
        with pytest.raises(DockerError): smoke.require_success()


def test_release_pr_gate_uses_no_app_secret():
    workflow = yaml.safe_load((ROOT / '.github/workflows/offline.yml').read_text())
    job = workflow['jobs']['offline']
    assert job['permissions'] == {'contents': 'read', 'actions': 'read'}
    step = next(s for s in job['steps'] if s.get('name') == 'Require live source App integration before main')
    assert step['if'] == "github.event_name == 'pull_request' && github.base_ref == 'main'"
    assert step['env'] == {'GH_TOKEN': '${{ github.token }}', 'EXPECTED_DEVELOP_SHA': '${{ github.event.pull_request.head.sha }}'}
    assert 'secrets.' not in json.dumps(workflow)
