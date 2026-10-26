"""Root-only containment of one failed empty-host bootstrap; never replay it.

The exact signed original database is retained before an atomic terminal update.
No Docker mutation, journal replacement, schema migration, fake success or LKG.
Install this reviewed capsule independently; ordinary CD cannot invoke this role.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

from installation.operator_bootstrap import exclusive, sync_directory
from scripts.cd.signatures import authenticate
from scripts.deployment_journal import Journal, SCHEMA, private_directory
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json

NAMESPACE = 'portfolio-bootstrap-containment-v1'
LIMIT = 16 * 1024 * 1024
FIELDS = {'version', 'id', 'run', 'candidate', 'host', 'daemon', 'manager_sha',
          'inventory_sha256', 'capsule_sha256', 'policy_id', 'journal_sha256', 'not_before', 'expires'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def journal_bytes(directory):
    """Existing private regular file only. Never initialize or follow a link."""
    directory = Path(directory)
    require(directory.is_dir(), 'containment-journal-required')
    with private_directory(directory) as fd:
        for suffix in ('-journal', '-wal', '-shm'):
            require(not os.path.lexists(directory / ('journal.sqlite3' + suffix)), 'containment-journal-sidecar')
        raw_fd = os.open('journal.sqlite3', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(raw_fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and
                    not before.st_mode & 0o077 and before.st_nlink == 1 and
                    0 < before.st_size <= LIMIT, 'containment-journal-metadata-invalid')
            raw = stream.read(LIMIT + 1)
            after = os.stat('journal.sqlite3', dir_fd=fd, follow_symlinks=False)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(len(raw) == before.st_size and identity(before) == identity(after), 'containment-journal-changed')
            return raw


@contextmanager
def read_journal(directory, lease):
    raw = journal_bytes(directory)
    db = sqlite3.connect((Path(directory) / 'journal.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=0)
    try:
        db.row_factory = sqlite3.Row
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        require(db.execute('PRAGMA user_version').fetchone()[0] == 2 and
                db.execute('PRAGMA application_id').fetchone()[0] == 1346651185 and
                {r[0] for r in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')} == set(SCHEMA) and
                db.execute('PRAGMA quick_check').fetchone()[0] == 'ok' and
                db.execute('PRAGMA foreign_key_check').fetchone() is None, 'containment-journal-invalid')
        journal = Journal(directory, lease)
        journal.db = db
        journal.validate()
        yield journal, raw
        require(journal_bytes(directory) == raw, 'containment-journal-changed')
    finally:
        db.close()


class BootstrapContainment:
    def __init__(self, *, installation, policy, lock, observe_empty, inventories, keys,
                 directory='/srv/portfolio/journal', state='/var/lib/portfolio-deployment-cd', clock=time.time):
        self.installation, self.policy, self.lock = installation, policy, lock
        self.observe_empty, self.inventories, self.keys = observe_empty, inventories, keys
        self.directory, self.state, self.clock = Path(directory), Path(state), clock

    def facts(self, journal, raw, run, *, completed=False):
        self.installation.verify()
        policy = self.policy.current()
        candidate = journal.bootstrap_containment_record(run, completed=completed)
        require(all(journal.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == count
                    for table, count in (('runs', 1), ('attempts', 1), ('authorizations', 1), ('good', 0))),
                'containment-first-bootstrap-only')
        require((candidate.host, candidate.daemon, candidate.manager_sha) ==
                (policy.document['host'], policy.document['daemon'], self.installation.manager_sha),
                'containment-installation-mismatch')
        return {'version': 1, 'run': run, 'candidate': candidate.identity, 'host': candidate.host,
                'daemon': candidate.daemon, 'manager_sha': candidate.manager_sha,
                'policy_id': policy.identity, 'journal_sha256': digest(raw), **self.inventories()}

    def prepare(self):
        with self.lock() as lease:
            self.observe_empty(lease)
            with read_journal(self.directory, lease) as (journal, raw):
                rows = journal.rows()
                require(len(rows) == 1, 'containment-first-bootstrap-only')
                facts = self.facts(journal, raw, rows[0]['run'])
            now = int(self.clock())
            decision = {**facts, 'id': uuid.uuid4().hex, 'not_before': now - 5, 'expires': now + 295}
            return {'status': 'prepared', 'decision': decision, 'executorStarted': False}

    def decision(self, envelope):
        decision, _ = authenticate(envelope, keys=self.keys, namespace=NAMESPACE)
        require(type(decision) is dict and set(decision) == FIELDS and type(decision['version']) is int and
                decision['version'] == 1 and
                all(type(decision[k]) is str and re.fullmatch('[0-9a-f]{32}', decision[k]) for k in ('id', 'run')) and
                all(type(decision[k]) is int for k in ('not_before', 'expires')) and
                0 <= decision['not_before'] <= self.clock() < decision['expires'] and
                0 < decision['expires'] - decision['not_before'] <= 300,
                'containment-decision-invalid-or-expired')
        return decision

    def retain(self, decision, envelope, raw):
        """Crash-safe exclusive evidence; a partial write never means approval."""
        target = self.state / ('bootstrap-containment-' + decision['id'])
        with private_directory(target):
            require(set(p.name for p in target.iterdir()) <= {'original.sqlite3', 'approval.json'},
                    'containment-audit-conflict')
            for name, data in (('original.sqlite3', raw), ('approval.json', envelope)):
                path = target / name
                if os.path.lexists(path):
                    info = path.lstat()
                    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                            not info.st_mode & 0o077 and info.st_nlink == 1 and info.st_size == len(data) and
                            path.read_bytes() == data, 'containment-audit-conflict')
                else:
                    exclusive(path, data)
            sync_directory(target)
            sync_directory(target.parent)
        return target

    def execute(self, envelope):
        decision = self.decision(envelope)  # authenticate before any audit writes
        with self.lock() as lease:
            self.observe_empty(lease)
            with read_journal(self.directory, lease) as (journal, raw):
                completed = bool(journal.rows()) and journal.rows()[0]['run_status'] == 'contained'
                facts = self.facts(journal, raw, decision['run'], completed=completed)
                if completed:
                    # A retry can finish reporting an already committed containment.
                    # It never replays either the bootstrap or the SQLite update.
                    target = self.state / ('bootstrap-containment-' + decision['id'])
                    require(target.is_dir() and not target.is_symlink(), 'containment-original-evidence-required')
                    with private_directory(target):
                        for name in ('original.sqlite3', 'approval.json'):
                            info = (target / name).lstat()
                            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                                    not info.st_mode & 0o077 and info.st_nlink == 1 and 0 < info.st_size <= LIMIT,
                                    'containment-original-evidence-required')
                        require((target / 'approval.json').read_bytes() == envelope,
                                'containment-original-evidence-required')
                        original = (target / 'original.sqlite3').read_bytes()
                    facts['journal_sha256'] = digest(original)
                require({k: decision[k] for k in facts} == facts, 'containment-exact-decision-changed')
            self.decision(envelope)
            if completed:
                self.retain(decision, envelope, original)
            else:
                self.retain(decision, envelope, raw)
                self.observe_empty(lease)
                self.installation.verify()
                self.policy.current()
                self.decision(envelope)
                require(journal_bytes(self.directory) == raw, 'containment-journal-changed')
                with Journal(self.directory, lease) as journal:
                    journal.contain_empty_bootstrap(decision['run'], decision['candidate'])
            return {'status': 'contained', 'run': decision['run'], 'candidate': decision['candidate'],
                    'executorStarted': False, 'bootstrapReplayed': False, 'lastGoodCreated': False}


def main():
    require(os.geteuid() == 0, 'containment-root-required')
    from scripts.cd.administrative import private, CAPSULE
    from scripts.cd.bridge import installation
    from scripts.cd.application import installed_policy
    from scripts.cd.quiescence import initial_review
    from scripts.production_integration.locking import production_host_lock, require_production_lease
    from scripts.production_integration.operator_approval import TRUST, trust_keys
    installed = installation()
    policy = installed_policy(installed)
    def observe(lease):
        require_production_lease(lease)
        initial_review(installed, policy)
    operation = BootstrapContainment(installation=installed, policy=policy, lock=production_host_lock,
        observe_empty=observe, keys=trust_keys(private(TRUST, 8192)), inventories=lambda: {
            'inventory_sha256': digest(private(Path('/opt/portfolio-deployment/installation.json'), 2*1024*1024)),
            'capsule_sha256': digest(private(CAPSULE / 'capsule.json', 2*1024*1024))})
    request = closed_json(sys.stdin.buffer.read(131073), 131072)
    require(request == {'op': 'prepare'} or
            (type(request) is dict and set(request) == {'op', 'envelope'} and request['op'] == 'execute'),
            'containment-operation-invalid')
    result = operation.prepare() if request['op'] == 'prepare' else operation.execute(
        base64.b64decode(request['envelope'], validate=True))
    print(canonical(result).decode())
    return 0
