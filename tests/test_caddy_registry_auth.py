"""Offline GHCR credential handoff: execute workflow shell with synthetic tools only."""
import base64
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'synthetic-workflow-registry-token'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('registry authentication regression attempted network access')
    monkeypatch.setattr(socket, 'socket', forbidden)


def publication():
    return yaml.safe_load((ROOT / '.github/workflows/caddy-publish.yml').read_text())['jobs']['publish']


def test_both_attestations_use_shared_standard_credentials_and_remain_required():
    job = publication()
    steps = job['steps']
    upload = next(s for s in steps if s.get('id') == 'image')
    acceptance = next(s for s in steps if 'acceptance --reference' in s.get('run', ''))
    attestations = [s for s in steps if s.get('id') in ('provenance', 'inputs')]
    assert [s['uses'] for s in attestations] == [
        'actions/attest-build-provenance@43d14bc2b83dec42d39ecae14e916627a18bb661',
        'actions/attest@f057fd524d485ac48d9b534c235aad15b5bb303f',
    ]
    assert upload['env'] == {'REGISTRY_TOKEN': '${{ github.token }}'}
    assert 'DOCKER_CONFIG' not in job.get('env', {})
    assert 'HOME' not in job.get('env', {})
    for step in (upload, acceptance, *attestations):
        assert 'if' not in step and not step.get('continue-on-error', False)
    for step in attestations:
        assert step['with']['subject-name'] == '${{ env.IMAGE }}'
        assert step['with']['subject-digest'] == '${{ steps.image.outputs.digest }}'
        assert step['with']['push-to-registry'] is True
        assert 'env' not in step  # Both actions inherit the same runner HOME.
        assert 'github-token' not in step['with']  # Default workflow token, never the App token.
    assert attestations[1]['with']['predicate-path'] == '${{ runner.temp }}/caddy-build/publication-inputs.json'
    cleanup = steps[-1]
    assert cleanup['name'] == 'Remove ephemeral registry authentication'
    assert cleanup['if'] == 'always()' and 'env' not in cleanup
    assert steps.index(upload) < steps.index(acceptance) < steps.index(attestations[0]) < steps.index(attestations[1])
    artifact = next(s for s in steps if s.get('name') == 'Preserve publication evidence')
    assert artifact['with']['path'].splitlines() == [
        '${{ runner.temp }}/publication.json', '${{ runner.temp }}/caddy-acceptance.json',
        '${{ runner.temp }}/caddy-build/publication-inputs.json',
        '${{ steps.provenance.outputs.bundle-path }}', '${{ steps.inputs.outputs.bundle-path }}',
    ]


@pytest.mark.parametrize('failure', ['none', 'login', 'push', 'receipt', 'acceptance', 'provenance', 'inputs', 'artifact'])
def test_credentials_reach_both_consumers_and_cleanup_on_success_or_failure(tmp_path, failure):
    home = tmp_path / 'home'
    home.mkdir()
    runner = tmp_path / 'runner'
    runner.mkdir()
    tools = tmp_path / 'bin'
    tools.mkdir()
    # An inherited nonstandard DOCKER_CONFIG must not divert the upload's login.
    diverted = tmp_path / 'diverted'
    diverted.mkdir()
    (diverted / 'config.json').write_text('{"sentinel":true}')
    config = home / '.docker/config.json'
    config.parent.mkdir()
    # Avoid helper-backed auth, which the pinned @sigstore/oci reader cannot read.
    config.write_text('{"credsStore":"unavailable-helper"}')
    mock = '''import base64, json, os, pathlib, sys
command = pathlib.Path(sys.argv[0]).name
if command == 'docker':
    assert sys.argv[1:3] == ['--config', os.environ['HOME'] + '/.docker']
    config = pathlib.Path(sys.argv[2]) / 'config.json'
    if sys.argv[3] == 'login':
        assert sys.argv[4:] == ['ghcr.io', '--username', 'test-actor', '--password-stdin']
        assert json.loads(config.read_text()) == {'auths': {}}
        token = sys.stdin.read()
        config.write_text(json.dumps({'auths': {'ghcr.io': {
            'auth': base64.b64encode(('test-actor:' + token).encode()).decode()}}}))
        sys.exit(1 if os.environ['FAILURE'] == 'login' else 0)
    assert sys.argv[3:] == ['push', os.environ['IMAGE'] + ':sha-' + os.environ['SOURCE_SHA']]
    sys.exit(1 if os.environ['FAILURE'] == 'push' else 0)
assert sys.argv[1:3] == ['-m', 'scripts.caddy_producer_receipt']
sys.exit(1 if os.environ['FAILURE'] == 'receipt' else 0)
'''
    for name in ('docker', 'python'):
        executable = tools / name
        executable.write_text('#!' + sys.executable + '\n' + mock)
        executable.chmod(0o700)
    env = dict(os.environ, HOME=str(home), RUNNER_TEMP=str(runner), PATH=str(tools) + os.pathsep + os.defpath,
               DOCKER_CONFIG=str(diverted), REGISTRY_TOKEN=TOKEN, GITHUB_ACTOR='test-actor',
               IMAGE='ghcr.io/test/caddy', SOURCE_SHA='a' * 40, GITHUB_OUTPUT=str(runner / 'output'), FAILURE=failure)
    steps = publication()['steps']
    upload = next(s for s in steps if s.get('id') == 'image')
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', upload['run']], env=env,
                            capture_output=True, text=True)
    assert result.returncode == (1 if failure in ('login', 'push', 'receipt') else 0)
    # This is the credential lookup contract in both pinned actions' bundled
    # @sigstore/oci getRegistryCredentials: homedir/.docker/config.json -> auths.
    if result.returncode == 0:
        for consumer in ('provenance', 'inputs'):
            if failure == 'acceptance':
                break
            auth = json.loads(config.read_text())['auths']['ghcr.io']['auth']
            assert base64.b64decode(auth).decode() == 'test-actor:' + TOKEN
            assert stat.S_IMODE(config.stat().st_mode) == 0o600
            assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
            if failure == consumer:
                break
    cleanup = subprocess.run(['bash', '-e', '-c', steps[-1]['run']], env=env,
                             capture_output=True, text=True)
    assert cleanup.returncode == 0 and not config.exists()
    assert (diverted / 'config.json').read_text() == '{"sentinel":true}'
    assert TOKEN not in result.stdout + result.stderr + cleanup.stdout + cleanup.stderr
    assert base64.b64encode(('test-actor:' + TOKEN).encode()).decode() not in result.stdout + result.stderr
    assert not cleanup.stdout and not cleanup.stderr


def test_cleanup_before_login_is_safe_and_idempotent(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    cleanup = publication()['steps'][-1]
    for _ in range(2):
        result = subprocess.run(['bash', '-e', '-c', cleanup['run']], env=env, capture_output=True, text=True)
        assert result.returncode == 0 and not result.stdout and not result.stderr
