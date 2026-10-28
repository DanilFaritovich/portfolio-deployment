"""CI scope decisions are offline; publication always retains full checks."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci_scope import documentation_only

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('paths, expected', [
    ((), True), (('README.md', 'docs/status.md', 'docs/diagram.png'), True),
    (('AGENTS.md', '.agents/skills/example/SKILL.md'), True),
    (('docs/status.md', 'scripts/cd/application.py'), False),
    (('.github/workflows/offline.yml',), False),
    (('requirements-dev.txt',), False), (('tests/fixtures/input.md',), False),
    (('docs/../scripts/ci_scope.py',), False), (('/docs/status.md',), False),
])
def test_documentation_scope(paths, expected):
    assert documentation_only(paths) is expected


def git(root, *args):
    return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                           '-C', str(root), *args], check=True, capture_output=True)


@pytest.mark.parametrize('event, change, expected', [
    ('pull_request', 'docs/status.md', 'false'),
    ('pull_request', 'scripts/runtime.py', 'true'),
    ('pull_request', None, 'false'),
    ('push', 'docs/status.md', 'true'),
    ('workflow_dispatch', 'docs/status.md', 'true'),
])
def test_real_entrypoint_checks_git_changes_and_keeps_publication_full(tmp_path, event, change, expected):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Offline Test')
    git(tmp_path, 'config', 'user.email', 'offline@example.test')
    git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'base')
    if change:
        path = tmp_path / change
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic fixture')
        git(tmp_path, 'add', change)
    git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'change')
    result = subprocess.run([sys.executable, '-m', 'scripts.ci_scope'], cwd=tmp_path,
        env={**os.environ, 'GITHUB_EVENT_NAME': event, 'PYTHONPATH': str(ROOT),
             'PYTHONDONTWRITEBYTECODE': '1'}, check=True, capture_output=True, text=True)
    assert 'run_heavy=' + expected in result.stdout.splitlines()


def test_missing_git_history_fails_closed(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'scripts.ci_scope'], cwd=tmp_path,
        env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request', 'PYTHONPATH': str(ROOT),
             'PYTHONDONTWRITEBYTECODE': '1'}, check=True, capture_output=True, text=True)
    assert 'run_heavy=true' in result.stdout.splitlines()
