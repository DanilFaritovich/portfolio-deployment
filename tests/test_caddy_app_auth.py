"""Offline workflow contracts and real publication-guard failure boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import urllib.error

import pytest
import yaml

from scripts import caddy_producer as producer
from scripts.production_integration import release_transport as transport
from test_caddy_producer import consumer_fixture

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'synthetic_installation_token'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('App authentication tests attempted network access')
    monkeypatch.setattr(socket, 'socket', forbidden)


def workflows():
    return tuple(yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
                 for name in ('caddy-image.yml', 'caddy-publish.yml'))


def test_app_token_scope_and_credential_isolation():
    caller, reusable = workflows()
    assert caller['jobs']['publish']['secrets'] == {
        'source-app-private-key': '${{ secrets.CADDY_SOURCE_APP_PRIVATE_KEY }}'}
    assert reusable[True]['workflow_call']['secrets']['source-app-private-key']['required'] is True
    job = reusable['jobs']['publish']
    steps = job['steps']
    issue_index = next(i for i, step in enumerate(steps) if step.get('id') == 'source-app')
    issue, guard = steps[issue_index:issue_index + 2]
    assert issue['uses'] == 'actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510'
    assert issue['with'] == {
        'client-id': '${{ vars.CADDY_SOURCE_APP_CLIENT_ID }}',
        'private-key': '${{ secrets.source-app-private-key }}',
        'owner': '${{ github.repository_owner }}',
        'repositories': '${{ github.event.repository.name }}',
        'permission-administration': 'read', 'permission-actions': 'read',
        'permission-checks': 'read', 'permission-contents': 'read',
        'skip-token-revoke': False,
    }
    assert guard['run'] == 'python -m scripts.caddy_producer publish-guard'
    assert guard['env'] == {'GH_TOKEN': '${{ steps.source-app.outputs.token }}'}
    # A failed credential check, mint or guard must stop every publication step.
    for step in steps[:issue_index + 2]:
        assert 'if' not in step and not step.get('continue-on-error', False)
    for workflow in (caller, reusable):
        for other_job in workflow['jobs'].values():
            assert 'GH_TOKEN' not in other_job.get('env', {})
            for step in other_job.get('steps', []):
                text = json.dumps(step)
                if step is not guard:
                    assert 'steps.source-app.outputs.token' not in text
                if step is not issue and step.get('name') != 'Require source App credentials':
                    assert 'secrets.source-app-private-key' not in text
        assert 'CADDY_SOURCE_POLICY_TOKEN' not in json.dumps(workflow)
    assert '${{ github.token }}' in json.dumps(steps[issue_index + 2:])
    for workflow in (caller, reusable):
        gate = workflow['jobs']['publish']['if']
        for condition in ("github.event_name == 'push'", "github.ref == 'refs/heads/main'", 'github.ref_protected'):
            assert condition in gate


@pytest.mark.parametrize('configured', ['true', 'false', ''])
def test_missing_app_credentials_stop_before_token_creation(configured):
    _, reusable = workflows()
    step = next(s for s in reusable['jobs']['publish']['steps'] if s.get('name') == 'Require source App credentials')
    assert step['env'] == {'SOURCE_APP_CONFIGURED':
        "${{ vars.CADDY_SOURCE_APP_CLIENT_ID != '' && secrets.source-app-private-key != '' }}"}
    result = subprocess.run(['bash', '-e', '-c', step['run']], capture_output=True, text=True,
                            env={'PATH': os.environ['PATH'], 'SOURCE_APP_CONFIGURED': configured})
    assert result.returncode == (0 if configured == 'true' else 1) and not result.stdout
    assert result.stderr.strip() == ('' if configured == 'true' else 'caddy-source-app-credentials-unavailable')


@pytest.mark.parametrize('failure', ['success', 'missing-token', 'empty-token', 'unavailable-app', 'expired-token',
    'administration-denied', 'actions-denied', 'checks-denied', 'contents-denied',
    'untrusted-app', 'untrusted-workflow', 'untrusted-source', 'weak-protection'])
def test_guard_cli_fails_closed_without_leaking_credentials(monkeypatch, capsys, failure):
    _, _, sha, docs, _, _ = consumer_fixture()
    for name, value in {'GITHUB_REPOSITORY': producer.REPOSITORY, 'GITHUB_EVENT_NAME': 'push',
        'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': sha,
        'GH_TOKEN': TOKEN}.items():
        monkeypatch.setenv(name, value)
    if failure == 'missing-token': monkeypatch.delenv('GH_TOKEN')
    if failure == 'empty-token': monkeypatch.setenv('GH_TOKEN', '')
    if failure == 'untrusted-source': monkeypatch.setenv('GITHUB_REPOSITORY', 'attacker/repo')
    if failure == 'weak-protection': docs['/branches/main/protection']['enforce_admins']['enabled'] = False
    if failure == 'untrusted-app':
        docs['/commits/' + sha + '/check-runs?filter=latest&per_page=100']['check_runs'][0]['app']['id'] = 999
    if failure == 'untrusted-workflow':
        docs['/actions/runs?head_sha=' + sha + '&per_page=100']['workflow_runs'][1]['repository']['id'] = 999
    calls = []
    def open_request(request, timeout):
        path = request.full_url.split('/repos/' + producer.REPOSITORY, 1)[1]
        calls.append(path)
        assert request.get_header('Authorization') == 'Bearer ' + TOKEN
        denied = {'administration-denied': '/branches/main/protection',
            'actions-denied': '/actions/runs?', 'checks-denied': '/commits/', 'contents-denied': '/branches/main'}
        if failure in ('unavailable-app', 'expired-token') or (
                failure in denied and path.startswith(denied[failure])):
            code = {'unavailable-app': 404, 'expired-token': 401}.get(failure, 403)
            raise urllib.error.HTTPError(request.full_url, code, TOKEN, {}, None)
        raw = json.dumps(deepcopy(docs[path])).encode()
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read1(self, size):
                result, self.raw = self.raw[:size], self.raw[size:]
                return result
        response = Response()
        response.raw = raw
        return response
    real_https = transport.HTTPSRead
    def https():
        result = real_https()
        result.opener = SimpleNamespace(open=open_request)
        return result
    monkeypatch.setattr(transport, 'HTTPSRead', https)
    monkeypatch.setattr('sys.argv', ['caddy_producer', 'publish-guard'])
    assert producer.main() == (0 if failure == 'success' else 1)
    captured = capsys.readouterr()
    assert captured.out == ('' if failure == 'success' else 'caddy-producer-inputs-failed\n') and not captured.err
    assert TOKEN not in captured.out + captured.err
    assert len(calls) < 20
    if failure in ('missing-token', 'empty-token', 'untrusted-source'): assert not calls
