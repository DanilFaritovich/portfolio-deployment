"""Keep required PR jobs present while avoiding documentation-only integration runs."""
import os
from pathlib import PurePosixPath
import subprocess

DOCUMENTS = frozenset({'README.md', 'README.ru.md', 'ARCHITECTURE.md', 'AGENTS.md',
                       'CHANGELOG.md', 'ASSETS-LICENSE.md', 'LICENSE'})


def documentation_only(paths):
    for name in paths:
        parts = PurePosixPath(name).parts
        if not name or name.startswith('/') or '..' in parts:
            return False
        if name not in DOCUMENTS and not name.startswith(('docs/', '.agents/')):
            return False
    return True


def changed_paths():
    # checkout fetch-depth: 2 includes both parents of GitHub's PR merge commit.
    # Disable rename detection so moves from documentation into code are included.
    try:
        raw = subprocess.run(['git', 'diff', '--no-renames', '--name-only', '-z',
                              'HEAD^1', 'HEAD', '--'], check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        if raw and not raw.endswith(b'\0'):
            return None
        return tuple(p.decode('utf-8') for p in raw.split(b'\0')[:-1])
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None


def main():
    # Main publication always runs every original build/check step.
    paths = changed_paths() if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request' else None
    heavy = paths is None or not documentation_only(paths)
    print('run_heavy=' + str(heavy).lower())
    print('reason=' + ('runtime-or-unclassified-change' if heavy else 'documentation-or-history-only'))


if __name__ == '__main__':
    main()
