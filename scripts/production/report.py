"""Closed report contract and private atomic local storage outside the checkout."""

import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

from scripts.production import InspectionError
from scripts.production.inventory import local_path, secure_file
from scripts.production.probes import COMMANDS, CATEGORIES

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas/production-inspection-v1.schema.json'
MAX_REPORT = 2 * 1024 * 1024


def validate(report):
    try:
        schema = json.loads(SCHEMA.read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
        if datetime.fromisoformat(report['finishedAt']) < datetime.fromisoformat(report['startedAt']):
            raise ValueError
        checks = report['checks']
        if len(checks) != len(COMMANDS) or {c['name'] for c in checks} != set(COMMANDS):
            raise ValueError
        for check in checks:
            if check['category'] != CATEGORIES[check['name']]:
                raise ValueError
            if check['status'] == 'passed' and not check['details']:
                raise ValueError
        return report
    except Exception:
        raise InspectionError('invalid-or-unsupported-report') from None


def directory():
    return Path.home() / '.local/share/portfolio-deployment/production-inspection'


def storage_check(path=None):
    """Read-only readiness check: do not create directories before approval."""
    path = local_path(str(path or directory()))
    checkout = Path(__file__).resolve().parents[2]
    if path == checkout or checkout in path.parents:
        raise InspectionError('report-storage-must-be-outside-checkout')
    existing = [p for p in [path, *path.parents] if p.exists()]
    for parent in existing:
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise InspectionError('unsafe-report-directory')
        if info.st_mode & 0o022 and not (info.st_uid == Path('/').stat().st_uid and info.st_mode & stat.S_ISVTX):
            raise InspectionError('unsafe-report-parent-permissions')
    if not existing or existing[0].stat().st_uid != os.getuid() or not os.access(existing[0], os.W_OK | os.X_OK):
        raise InspectionError('report-storage-not-writable')
    if path.exists():
        safe_directory(path)
        destination = path / 'latest.json'
        if destination.exists() or destination.is_symlink():
            secure_file(destination, private=True)
    return path


def safe_directory(path, *, create=False):
    path = local_path(str(path))
    checkout = Path(__file__).resolve().parents[2]
    if path == checkout or checkout in path.parents:
        raise InspectionError('report-storage-must-be-outside-checkout')
    if create:
        # Build each directory without following links; preserve existing modes.
        for parent in reversed([path, *path.parents][:-1]):
            if not parent.exists():
                parent.mkdir(mode=0o700)
            if parent.is_symlink() or not parent.is_dir():
                raise InspectionError('unsafe-report-directory')
    for parent in path.parents:
        info = parent.stat()
        if info.st_mode & 0o022 and not (info.st_uid == Path('/').stat().st_uid and info.st_mode & stat.S_ISVTX):
            raise InspectionError('unsafe-report-parent-permissions')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise InspectionError('report-directory-requires-0700')
    return path


def save(report, path=None):
    data = json.dumps(validate(report), ensure_ascii=True, allow_nan=False, indent=2).encode() + b'\n'
    if len(data) > MAX_REPORT:
        raise InspectionError('report-size-limit')
    root = safe_directory(storage_check(path), create=True)
    destination = root / 'latest.json'
    if destination.exists() or destination.is_symlink():
        secure_file(destination, private=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.inspection-', dir=root)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def load(path=None):
    try:
        root = safe_directory(path or directory())
        source = root / 'latest.json'
        secure_file(source, private=True)
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InspectionError('invalid-report-file')
            data = stream.read(MAX_REPORT + 1)
        if len(data) > MAX_REPORT:
            raise InspectionError('report-size-limit')
        return validate(json.loads(data))
    except (OSError, ValueError, RecursionError):
        raise InspectionError('report-missing-corrupt-or-unsupported: run an explicitly authorized inspection first') from None


def summary(report):
    validate(report)
    target = report['target']
    counts = {status: sum(c['status'] == status for c in report['checks']) for status in ('passed', 'blocked', 'unknown')}
    lines = [f"{target['user']}@{target['host']}:{target['port']} — {report['finishedAt']}",
             ', '.join(f'{count} {status}' for status, count in counts.items())]
    lines.extend('blocker: ' + reason for reason in report['blockers'])
    lines.extend('conflict: ' + reason for reason in report['conflicts'][:8])
    if len(report['conflicts']) > 8:
        lines.append(f"{len(report['conflicts']) - 8} additional conflicts in the private JSON report.")
    lines.append('Inspection is not deployment authorization; public DNS/TLS/GHCR remain unverified.')
    return '\n'.join(lines)
