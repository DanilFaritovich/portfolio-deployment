#!/usr/bin/python3
"""Installed-only application CD entry; no shell, daemon or request-selected code."""
import json
import os
from pathlib import Path
import stat
import sys


def main():
    if (len(sys.argv) != 1 or os.geteuid() != 0 or not sys.flags.isolated or
        not sys.flags.no_site or not sys.dont_write_bytecode):
        return 2
    root = Path('/opt/portfolio-deployment')
    for path in (Path('/'), Path('/opt'), root, root / 'vendor'):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return 2
    sys.path[0:0] = [str(root), str(root / 'vendor')]
    from scripts.production_integration.installation import SealedInstallation, protected
    protected(root / 'installation.json')
    document = json.loads((root / 'installation.json').read_bytes())
    installation = SealedInstallation(document['manager_sha'], tuple(map(tuple, document['files'])))
    installation.verify()
    return run_application(installation)


def run_application(installation):
    # Called only after the sealed inventory passed verification. Public codes
    # are allowlisted; arbitrary exceptions and private data stay off Actions.
    from scripts.cd.diagnostics import blocked
    from scripts.production_integration.operator_approval import canonical
    try:
        from scripts.cd.application import main as application
        return application(installation)
    except Exception as error:
        print(canonical(blocked(error, 'cd-installed-entry-failed')).decode())
        return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"reason":"cd-installed-entry-failed","status":"blocked"}')
        sys.exit(2)
