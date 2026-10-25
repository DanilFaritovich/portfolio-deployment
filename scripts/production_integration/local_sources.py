"""Concrete bounded host reads for fixed sealed wiring; never an attestor.

Raw fresh facts are inputs to independent HostContracts/SourceTrust attestation.
No saved report, inferred absence, backup assertion or ownership adoption.
"""
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from scripts.deployment_journal import Journal, SCHEMA
from scripts.docker_adapter import DockerError, run_bounded
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.docker import ProductionDockerAdapter
from scripts.production_integration.installation import protected
from scripts.production_integration.locking import require_production_lease
from scripts.production_integration.storage import validate_storage_directory

PATHS = tuple('/srv/portfolio/' + n for n in ('locks', 'state', 'journal', 'requests', 'caddy-config',
                                          'caddy-lkg', 'certificates', 'caddy-data', 'caddy-admin', 'temporary'))
JOURNAL_DIRECTORY = '/srv/portfolio/journal'
COMMANDS = {
    'routes4': ('/usr/bin/ip', '-j', '-4', 'route', 'show', 'table', 'all'),
    'routes6': ('/usr/bin/ip', '-j', '-6', 'route', 'show', 'table', 'all'),
    'rules4': ('/usr/bin/ip', '-j', '-4', 'rule', 'show'),
    'rules6': ('/usr/bin/ip', '-j', '-6', 'rule', 'show'),
    'listeners': ('/usr/bin/ss', '-H', '-lntu'),
}


@dataclass(frozen=True)
class LocalHostFacts:
    identity: tuple[str, str]
    captured_at: float
    network: dict
    storage: tuple[dict, ...]
    source: str = 'installed-local-kernel-v1'


class MachineIdentity:
    def __call__(self):
        path = Path('/etc/machine-id')
        protected(path)
        with path.open('rb') as stream:
            raw = stream.read(34)
        if not re.fullmatch(rb'[0-9a-f]{32}\n?', raw):
            raise DockerError('production-machine-identity-unavailable')
        return raw.decode().strip()


def installed_engine(installation, contract):
    """Read permission/host/daemon expectations are reviewed installation code."""
    installation.verify()
    engine = ProductionDockerAdapter(contract, MachineIdentity())
    engine.binary = '/usr/bin/docker'
    return engine


class LocalHostSources:
    def __init__(self, installation, engine, *, clock=time.monotonic):
        self.installation, self.engine, self.clock = installation, engine, clock

    def collect(self, *, before_observation=None):
        """Notify a composite reader after seal verification, before mutable IO.

        The notification never skips verification or supplies a trusted timestamp.
        The caller starts its own clock; standalone reads retain their own bound.
        """
        try:
            return self._collect(before_observation=before_observation)
        except DockerError:
            raise
        except Exception:
            raise DockerError('production-host-source-unavailable') from None

    def _collect(self, *, before_observation=None):
        self.installation.verify()
        if before_observation is not None:
            before_observation()
        start = self.clock()
        before = self.engine.identity()
        network = {}
        for name, argv in COMMANDS.items():
            # Absolute tools and exact bytes must also be sealed installation pins.
            pins = dict(self.installation.files)
            if argv[0] not in pins:
                raise DockerError('production-host-source-tool-unpinned')
            remaining = 5 - (self.clock() - start)
            if remaining <= 0:
                raise DockerError('production-host-source-deadline')
            raw = run_bounded(list(argv), timeout=min(2, remaining))
            if name == 'listeners':
                rows = raw.decode('ascii').splitlines()
                if len(rows) > 512:
                    raise DockerError('production-listener-inventory-incomplete')
                ports = []
                for row in rows:
                    parts = row.split()
                    if len(parts) != 6 or parts[0] not in ('tcp', 'udp'):
                        raise DockerError('production-listener-inventory-incomplete')
                    endpoint = parts[4]
                    try:
                        port = int(endpoint.rsplit(':', 1)[1])
                        if not 1 <= port <= 65535:
                            raise ValueError
                    except (ValueError, IndexError):
                        raise DockerError('production-listener-inventory-incomplete') from None
                    ports.append({'protocol': parts[0], 'address': endpoint, 'port': port})
                network[name] = ports
            else:
                try:
                    rows = parse_snapshot(raw)
                    if not isinstance(rows, list) or len(rows) > 512 or not all(isinstance(r, dict) for r in rows):
                        raise ValueError
                    network[name] = rows
                except (ValueError, TypeError, UnicodeError):
                    raise DockerError('production-routing-inventory-incomplete') from None
        storage = []
        for path in PATHS:
            validate_storage_directory(path, uid=0)
            info, capacity = os.lstat(path), os.statvfs(path)
            storage.append({'path': path, 'uid': info.st_uid, 'gid': info.st_gid,
                            'mode': stat.S_IMODE(info.st_mode), 'device': info.st_dev, 'inode': info.st_ino,
                            'availableBytes': capacity.f_bavail * capacity.f_frsize,
                            'availableInodes': capacity.f_favail})
        if self.engine.identity() != before or self.clock() - start > 5:
            raise DockerError('production-host-source-changed-or-expired')
        return LocalHostFacts(before, self.clock(), network, tuple(storage))


class ReadOnlyJournalSource:
    """No initialization, migration, recovery replay or SQLite write connection.

    Caller must hold canonical host lease. Existing Stage 06 validation is reused.
    Missing DB is reported explicitly, never asserted to be a bootstrap baseline.
    """
    def read(self, lease):
        try:
            return self._read(lease)
        except DockerError:
            raise
        except Exception:
            raise DockerError('production-journal-source-unavailable') from None

    def _read(self, lease):
        require_production_lease(lease)
        validate_storage_directory(JOURNAL_DIRECTORY, uid=0)
        path = Path(JOURNAL_DIRECTORY) / 'journal.sqlite3'
        for suffix in ('-journal', '-wal', '-shm'):
            if Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink():
                raise DockerError('production-journal-recovery-sidecar-present')
        if not path.exists() and not path.is_symlink():
            return {'database': 'absent', 'recovery': 'unestablished'}
        protected(path)
        info = path.stat()
        if info.st_mode & 0o077 or info.st_size > 16 * 1024 * 1024:
            raise DockerError('production-journal-metadata-invalid')
        deadline = time.monotonic() + 2
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            if (db.execute('PRAGMA user_version').fetchone()[0] != 2 or
                db.execute('PRAGMA application_id').fetchone()[0] != 1346651185 or
                {r[0] for r in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')} != set(SCHEMA) or
                db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or
                db.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise DockerError('production-journal-schema-unverified')
            journal = Journal(JOURNAL_DIRECTORY, lease)
            journal.db = db
            journal.validate()
            result = {'database': 'present', 'incomplete': journal.incomplete(),
                      'authorizations': journal.authorization_recovery(),
                      'bootstrap': journal.last_good('manager-bootstrap', 'gateway')}
            if result['bootstrap'] is not None:
                row = db.execute('SELECT payload,state FROM authorizations WHERE nonce=?',
                                 (result['bootstrap']['run'],)).fetchone()
                result['bootstrapAuthorization'] = dict(row) if row is not None else None
        require_production_lease(lease)
        if (path.stat().st_dev, path.stat().st_ino) != (info.st_dev, info.st_ino):
            raise DockerError('production-journal-identity-changed')
        return result
