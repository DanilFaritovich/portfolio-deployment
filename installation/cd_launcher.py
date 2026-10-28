#!/usr/bin/python3
"""Independently installed capsule launcher. Exact sudoers argv supplies the role."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path('/usr/local/lib/portfolio-deployment-cd')


def main():
    if (os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('automation', 'observer', 'operator-recovery', 'operator-prebootstrap', 'operator-bootstrap-containment', 'operator-bootstrap-verification-recovery', 'operator-first-rollout-recovery', 'operator-application-update-recovery') or
        not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode):
        return 2
    for path in (*reversed(ROOT.parents), ROOT):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return 2
    manifest = ROOT / 'capsule.json'
    info = manifest.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 2097152:
        return 2
    pins = json.loads(manifest.read_bytes())['files']
    actual, pending = set(), [ROOT]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            return 2
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            if path != manifest:
                name = str(path)
                if name not in pins or hashlib.sha256(path.read_bytes()).hexdigest() != pins[name]:
                    return 2
                actual.add(name)
        else:
            return 2
        if len(actual) + len(pending) > 8192:
            return 2
    for name, digest in pins.items():
        path = Path(name)
        if path.is_relative_to(ROOT):
            if name not in actual:
                return 2
        else:
            for parent in reversed(path.parents):
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    return 2
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o022:
                return 2
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return 2
    sys.path[0:0] = [str(ROOT), str(ROOT / 'vendor')]
    if sys.argv[1] == 'operator-prebootstrap':
        # Root administrative channel only; never in the deploy-account sudoers.
        from scripts.cd.administrative import main
        return main()
    if sys.argv[1] == 'operator-recovery':
        # Never present in deploy-account sudoers/authorized_keys.
        from scripts.cd.bridge import recover_main
        return recover_main()
    if sys.argv[1] == 'operator-bootstrap-containment':
        # Independent root channel only, absent from forced SSH keys and sudoers.
        from scripts.cd.bootstrap_containment import main
        return main()
    if sys.argv[1] == 'operator-bootstrap-verification-recovery':
        from scripts.cd.bootstrap_recovery import main
        return main()
    if sys.argv[1] == 'operator-application-update-recovery':
        from scripts.cd.update_recovery_runtime import main
        return main()
    if sys.argv[1] == 'operator-first-rollout-recovery':
        # Independent root channel only; never delegated to automation/observer.
        from scripts.cd.rollout_recovery import main
        return main()
    from scripts.cd.bridge import main as bridge
    return bridge(sys.argv[1])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"status":"blocked","reason":"cd-trusted-intake-failed"}')
        sys.exit(2)
