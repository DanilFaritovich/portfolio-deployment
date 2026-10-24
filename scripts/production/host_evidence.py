"""Offline, redacted tool evidence review of the C4.1 diagnostic transcript.

Untrusted historical text is never a pin inventory, HostPolicy, SourceTrust proof
or approval. No host commands, authority imports, report copying or file writes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 16384
TOOLS = {
    'python': '/usr/bin/python3.12',
    'docker': '/usr/bin/docker',
    'compose': '/usr/libexec/docker/cli-plugins/docker-compose',
    'gh': '/usr/bin/gh',
    'ip': '/usr/bin/ip',
    'ss': '/usr/bin/ss',
    'ssh-keygen': '/usr/bin/ssh-keygen',
}
PLUGINS = tuple(d + '/docker-compose' for d in (
    '/usr/local/lib/docker/cli-plugins', '/usr/local/libexec/docker/cli-plugins',
    '/usr/lib/docker/cli-plugins', '/usr/libexec/docker/cli-plugins'))
# stat -c '%n %F %u:%g %a %d:%i %h', followed by readlink -f and sha256sum.
METADATA = re.compile(r'(/usr/[^ ]+) (regular file|symbolic link|directory) (\d{1,10}):(\d{1,10}) ([0-7]{3,4}) \d{1,20}:\d{1,20} (\d{1,10})')
SHA = re.compile(r'([0-9a-f]{64})  (/usr/[^ ]+)')


class EvidenceError(ValueError):
    pass


def read_report(path):
    """Bounded regular-file intake; never follow a link or block on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise EvidenceError('host-evidence-input-invalid')
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                raise EvidenceError('host-evidence-input-changed')
            return raw
    except OSError:
        raise EvidenceError('host-evidence-input-unavailable') from None


def assess(raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise EvidenceError('host-evidence-input-limit')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError:
        raise EvidenceError('host-evidence-input-encoding') from None
    if len(lines) > MAX_LINES or any(len(line) > 262144 for line in lines):
        raise EvidenceError('host-evidence-input-limit')
    records = {}
    missing = set()
    for index, line in enumerate(lines):
        if line.startswith('MISSING '):
            missing.add(line[8:])
        match = METADATA.fullmatch(line)
        if match:
            path, kind, uid, gid, mode, links = match.groups()
            following = lines[index + 1:index + 3]
            checksum = SHA.fullmatch(following[1]) if len(following) == 2 else None
            records.setdefault(path, []).append((kind, int(uid), int(gid), int(mode, 8), int(links),
                bool(following and following[0] == path and checksum and checksum[2] == path)))

    tools = []
    for name, path in TOOLS.items():
        rows = records.get(path, [])
        reasons = []
        status = 'OBSERVED'
        if path in missing:
            status, reasons = 'BLOCKED', ['tool-explicitly-missing']
        elif not rows:
            status, reasons = 'UNKNOWN', ['resolved-file-metadata-and-sha256-required']
        elif len(rows) != 1:
            status, reasons = 'BLOCKED', ['duplicate-tool-records']
        else:
            kind, uid, gid, mode, links, checksum = rows[0]
            if kind != 'regular file' or uid != 0 or gid != 0 or mode & 0o022 or not mode & 0o100 or links != 1:
                status, reasons = 'BLOCKED', ['tool-metadata-unsafe']
            elif not checksum:
                status, reasons = 'UNKNOWN', ['adjacent-resolved-path-and-sha256-required']
        tools.append({'tool': name, 'path': path, 'status': status, 'reasons': reasons})

    # All four locations must be explicitly accounted for. Never infer absence
    # from a permission error, truncated transcript or a missing stat record.
    plugins = [p for p in PLUGINS if p in records]
    if any(len(records[p]) != 1 or p in missing for p in plugins) or len(plugins) > 1:
        compose = 'BLOCKED'
    elif plugins == [TOOLS['compose']] and all(p in missing for p in PLUGINS if p not in plugins):
        compose = next(t['status'] for t in tools if t['tool'] == 'compose')
    elif all(p in missing for p in PLUGINS):
        compose = 'BLOCKED'
    else:
        compose = 'UNKNOWN'

    def version(pattern):
        values = re.findall(pattern, '\n'.join(lines), re.M)
        return values[0] if len(values) == 1 else None

    versions = {
        'python': version(r'^(3\.12\.[0-9]{1,4}) \([^\n]*\) \[GCC [0-9.]+\]$'),
        'abi': version(r'^(cpython-312-x86_64-linux-gnu)$'),
        'docker': version(r'^Docker version ([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}), build [a-f0-9]+$'),
        # The preceding Docker line distinguishes Compose from SQLite's version.
        'compose': version(r'^Docker version [^\n]+\nv?([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})$'),
        'gh': version(r'^gh version ([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}) \([0-9-]+\)$'),
    }
    gh = 'UNKNOWN' if versions['gh'] is None else ('OBSERVED' if versions['gh'] == '2.102.0' else 'BLOCKED')
    return {'schemaVersion': 1, 'evidence': 'unattested-historical-transcript',
            'installation': 'BLOCKED', 'productionAuthorized': False,
            'tools': tools, 'versions': versions, 'singleFixedComposePlugin': compose,
            'requiredGhVersion': gh,
            'protectedToolAncestry': 'UNKNOWN', 'dependencyProvenance': 'UNKNOWN',
            'runtimeCompatibility': 'UNKNOWN',
            'remaining': ['fresh-independent-host-attestation', 'complete-protected-external-inventory',
                          'phase-host-policy-and-prerequisite-authority', 'independent-admin-and-operator-approvals',
                          'bootstrap-source-and-public-postcondition-authorities', 'backup-restore-and-containment-evidence']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    try:
        result = assess(read_report(Path(args.report)))
    except EvidenceError as error:
        print(json.dumps({'error': str(error), 'productionAuthorized': False}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    # Successful analysis never means successful installation readiness.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
