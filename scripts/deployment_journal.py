"""Private SQLite journal v2, including irreversible authorization consumption.

Only closed metadata is persisted, never runtime values, logs or arbitrary errors.
DELETE journal + synchronous FULL gives SQLite crash durability on a functioning
local filesystem. Disk loss, unsafe storage and backups remain operator concerns.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import math
from pathlib import Path
import re
import sqlite3
import stat
import time
import json

from scripts.deployment import host_lock, owns_host_lock


class JournalError(RuntimeError):
    pass


TRANSITIONS = {
    "prepared": {"applying", "verifying", "recovery-required", "recovered-verified"},
    "applying": {"verifying", "failed", "outcome-unknown", "recovery-required", "contained"},
    "verifying": {"succeeded", "failed", "recovery-required"},
    "failed": {"rolling-back", "recovery-required"},
    "outcome-unknown": {"recovery-required"},
    "rolling-back": {"rolled-back-verified", "recovery-required"},
    "succeeded": {"recovered-verified"}, "rolled-back-verified": {"recovered-verified"},
    "recovered-verified": set(), "recovery-required": set(), "contained": set(),
}
REASONS = {"intent-recorded", "mutation-started", "mutation-completed", "mutation-failed",
           "mutation-outcome-unknown", "readiness-verified", "readiness-failed", "fresh-state-unavailable",
           "dependency-readiness-unverified", "gateway-unverified", "rollback-started", "rollback-verified",
           "rollback-failed", "rollback-unsafe", "recovery-analysis-required", "runtime-unverified", "recovery-verified",
           "empty-bootstrap-contained"}
SCHEMA = (
    "CREATE TABLE runs (id TEXT PRIMARY KEY, application TEXT NOT NULL, revision TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, updated REAL NOT NULL)",
    "CREATE TABLE attempts (run TEXT NOT NULL REFERENCES runs(id), component TEXT NOT NULL, previous TEXT, target TEXT NOT NULL, runtime TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, readiness TEXT NOT NULL, probes INTEGER NOT NULL, consecutive INTEGER NOT NULL, updated REAL NOT NULL, PRIMARY KEY(run,component))",
    "CREATE TABLE events (sequence INTEGER PRIMARY KEY, run TEXT NOT NULL, component TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, readiness TEXT NOT NULL, probes INTEGER NOT NULL, consecutive INTEGER NOT NULL, at REAL NOT NULL, FOREIGN KEY(run,component) REFERENCES attempts(run,component))",
    "CREATE TABLE good (application TEXT NOT NULL, component TEXT NOT NULL, run TEXT NOT NULL, PRIMARY KEY(application,component), FOREIGN KEY(run,component) REFERENCES attempts(run,component))",
)
AUTHORIZATION_SCHEMA = "CREATE TABLE authorizations (nonce TEXT PRIMARY KEY, candidate TEXT NOT NULL UNIQUE, operator TEXT NOT NULL, not_before REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)"
LEGACY_SCHEMA = SCHEMA
SCHEMA = (*SCHEMA, AUTHORIZATION_SCHEMA)
IMAGE = r"ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}"
NAME = r"[a-z][a-z0-9-]{0,62}"


@dataclass(frozen=True)
class Intent:
    component: str
    previous: str | None
    target: str
    runtime: str  # hash of image-excluded effective application configuration


def valid_intent(i):
    return (re.fullmatch(NAME, i.component) and re.fullmatch(IMAGE, i.target) and
            (i.previous is None or re.fullmatch(IMAGE, i.previous)) and re.fullmatch(r"[0-9a-f]{64}", i.runtime))


@contextmanager
def private_directory(directory):
    """Walk without symlinks, pin directory fd, reject writable unsafe ancestors."""
    path = Path(directory)
    if not path.is_absolute() or ".." in path.parts:
        raise JournalError("unsafe-journal-path")
    system_uid = os.stat("/").st_uid  # root may be UID-mapped by a sandbox
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            leaf = index == len(path.parts) - 2
            if leaf:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            if (info.st_uid not in (system_uid, os.geteuid()) or
                (leaf and (info.st_uid != os.geteuid() or info.st_mode & 0o077)) or
                (not leaf and info.st_mode & 0o022 and not (info.st_uid == system_uid and info.st_mode & stat.S_ISVTX))):
                raise JournalError("unsafe-journal-permissions")
        if path == Path("/"):
            raise JournalError("unsafe-journal-path")
        yield fd
    except OSError:
        raise JournalError("journal-path-unavailable") from None
    finally:
        os.close(fd)


class Journal:
    def __init__(self, directory, lease):
        self.directory, self.lease = directory, lease
        self.db = None
        self._directory = None

    def _locked(self):
        if not owns_host_lock(self.lease):
            raise JournalError("journal-host-lock-required")

    def __enter__(self):
        self._locked()
        self._directory = private_directory(self.directory)
        fd = self._directory.__enter__()
        try:
            for name in ("journal.sqlite3", "journal.sqlite3-journal", "journal.sqlite3-wal", "journal.sqlite3-shm"):
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 16 * 1024 * 1024):
                    raise JournalError("unsafe-journal-file")
            raw = os.open("journal.sqlite3", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            os.close(raw)
            # A pinned private directory prevents ancestor/symlink replacement.
            self.db = sqlite3.connect(f"/proc/self/fd/{fd}/journal.sqlite3", timeout=0, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").fetchall()
            if version == 0 and not tables:
                with self.transaction():
                    for sql in SCHEMA:
                        self.db.execute(sql)
                    self.db.execute("PRAGMA user_version=2")
                    self.db.execute("PRAGMA application_id=1346651185")
            elif version == 1 and {r[0] for r in tables} == set(LEGACY_SCHEMA) and self.db.execute("PRAGMA application_id").fetchone()[0] == 1346651185:
                with self.transaction():
                    self.db.execute(AUTHORIZATION_SCHEMA)
                    self.db.execute("PRAGMA user_version=2")
                    self.validate()  # invalid legacy history must roll back migration
            elif version != 2:
                raise JournalError("unsupported-journal-version")
            if (self.db.execute("PRAGMA application_id").fetchone()[0] != 1346651185 or
                {r[0] for r in self.db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")} != set(SCHEMA) or
                self.db.execute("PRAGMA quick_check").fetchall()[0][0] != "ok" or
                self.db.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise JournalError("corrupt-journal")
            self.validate()
            return self
        except BaseException as error:
            self.__exit__(None, None, None)
            if isinstance(error, (sqlite3.Error, TypeError, ValueError, KeyError, OverflowError)):
                raise JournalError("journal-unavailable-or-corrupt") from None
            raise

    def __exit__(self, *args):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._directory is not None:
            self._directory.__exit__(*args)
            self._directory = None

    @contextmanager
    def transaction(self):
        self._locked()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.execute("COMMIT")
        except BaseException as error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise JournalError("journal-write-failed") from None
            raise

    def rows(self):
        self._locked()
        return [dict(r) for r in self.db.execute("SELECT attempts.*, runs.application, runs.revision, runs.status AS run_status, runs.reason AS run_reason, runs.updated AS run_updated FROM attempts JOIN runs ON runs.id=attempts.run ORDER BY run,component")]

    def validate(self):
        approvals = self.db.execute('SELECT * FROM authorizations').fetchall()
        if len(approvals) > 10000:
            raise JournalError('authorization-retention-required')
        for a in approvals:
            from scripts.production_integration.authorization import OperationCandidate
            try:
                candidate = OperationCandidate(**json.loads(a['payload']))
                if candidate.identity != a['candidate']:
                    raise ValueError
            except Exception:
                raise JournalError('corrupt-authorization-candidate') from None
            if (not re.fullmatch(r'[0-9a-f]{32}', a['nonce']) or
                not re.fullmatch(r'sha256:[0-9a-f]{64}', a['candidate']) or
                not re.fullmatch(r'[A-Za-z0-9@._-]{1,128}', a['operator']) or
                not all(type(a[k]) in (float, int) and math.isfinite(a[k]) for k in ('not_before', 'expires')) or
                not 0 < a['expires'] - a['not_before'] <= 3600 or
                a['state'] not in ('consumed', 'succeeded', 'blocked', 'recovery-required')):
                raise JournalError('corrupt-authorization-ledger')
        counts = [self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("runs", "attempts", "events")]
        if any(n > cap for n, cap in zip(counts, (10000, 10000, 100000))):
            raise JournalError("journal-retention-required")
        rows = self.rows()
        if len(rows) > 10000:
            raise JournalError("journal-retention-required")
        for r in rows:
            if (not re.fullmatch(r"[0-9a-f]{32}", r["run"]) or not re.fullmatch(NAME, r["application"]) or
                not re.fullmatch(r"[0-9a-f]{40}", r["revision"]) or
                not valid_intent(Intent(r["component"], r["previous"], r["target"], r["runtime"])) or
                (type(r["updated"]) not in (int, float) or not math.isfinite(r["updated"])) or
                r["run_reason"] not in REASONS or type(r["run_updated"]) not in (int, float) or not math.isfinite(r["run_updated"]) or
                r["run_status"] not in ("active", "succeeded", "recovery-required", "recovered", "contained") or
                ((r["run_status"] == "contained") != (r["state"] == "contained")) or
                ((r["run_status"] == "contained") != (r["run_reason"] == "empty-bootstrap-contained")) or
                ((r["state"] == "contained") != (r["reason"] == "empty-bootstrap-contained")) or
                (r["run_status"] == "succeeded" and r["state"] != "succeeded") or
                ((r["run_status"] == "recovered") != (r["state"] == "recovered-verified")) or
                ((r["run_status"] == "recovered") != (r["run_reason"] == "recovery-verified")) or
                (r["run_status"] == "recovered" and r["run_updated"] != r["updated"]) or
                r["state"] not in TRANSITIONS or r["reason"] not in REASONS or
                r["readiness"] not in ("unknown", "running", "ready", "failed", "verified") or
                type(r["probes"]) is not int or type(r["consecutive"]) is not int or not 0 <= r["consecutive"] <= r["probes"] <= 3001 or
                (r["state"] in ("succeeded", "rolled-back-verified", "recovered-verified") and
                 (r["readiness"] != "verified" or r["consecutive"] < 2))):
                raise JournalError("corrupt-journal-record")
            events = [dict(e) for e in self.db.execute("SELECT * FROM events WHERE run=? AND component=? ORDER BY sequence", (r["run"], r["component"]))]
            state = None
            for e in events:
                if ((state is None and (e["state"] != "prepared" or e["reason"] != "intent-recorded")) or
                    (state is not None and e["state"] not in TRANSITIONS.get(state, set())) or
                    e["reason"] not in REASONS or e["readiness"] not in ("unknown", "running", "ready", "failed", "verified") or
                    type(e["probes"]) is not int or type(e["consecutive"]) is not int or not 0 <= e["consecutive"] <= e["probes"] <= 3001 or
                    type(e["at"]) not in (int, float) or not math.isfinite(e["at"]) or
                    ((e["state"] == "recovered-verified") != (e["reason"] == "recovery-verified")) or
                    ((e["state"] == "contained") != (e["reason"] == "empty-bootstrap-contained")) or
                    (e["state"] in ("succeeded", "rolled-back-verified", "recovered-verified") and
                     (e["readiness"] != "verified" or e["consecutive"] < 2))):
                    raise JournalError("corrupt-journal-events")
                state = e["state"]
            if not events or any(events[-1][k] != r[k] for k in ("state", "reason", "readiness", "probes", "consecutive")) or events[-1]["at"] != r["updated"]:
                raise JournalError("incomplete-journal-record")
        for run in {r["run"] for r in rows if r["run_status"] == "recovered"}:
            self.recovery_baseline(run, completed=True)
        for run in {r["run"] for r in rows if r["run_status"] == "contained"}:
            self.bootstrap_containment_record(run, completed=True)
        for good in self.db.execute("SELECT * FROM good"):
            run = [r for r in rows if r["run"] == good["run"]]
            if (not run or any(r["state"] != "succeeded" or r["run_status"] != "succeeded" for r in run) or
                not any(r["component"] == good["component"] and r["application"] == good["application"] for r in run)):
                raise JournalError("corrupt-last-known-good")
        latest = {r["application"]: r["id"] for r in self.db.execute("SELECT id,application FROM runs WHERE status='succeeded' ORDER BY rowid")}
        for application, run in latest.items():
            expected = {r["component"] for r in rows if r["run"] == run}
            actual = {g["component"] for g in self.db.execute("SELECT component FROM good WHERE application=? AND run=?", (application, run))}
            if expected != actual:
                raise JournalError("incomplete-last-known-good")
        for run in self.db.execute("SELECT id,created FROM runs"):
            if (type(run[1]) not in (int, float) or not math.isfinite(run[1]) or
                not any(r["run"] == run[0] for r in rows)):
                raise JournalError("incomplete-journal-run")

    def prepare(self, run, application, revision, intents):
        if (not re.fullmatch(r"[0-9a-f]{32}", run) or not re.fullmatch(NAME, application) or
            not re.fullmatch(r"[0-9a-f]{40}", revision) or not intents or len(intents) > 32 or
            len({i.component for i in intents}) != len(intents) or not all(valid_intent(i) for i in intents)):
            raise JournalError("invalid-journal-intent")
        if len(self.rows()) > 10000 - len(intents):
            raise JournalError("journal-retention-required")
        if self.incomplete():
            raise JournalError("unfinished-operation-requires-analysis")
        with self.transaction():
            now = time.time()
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (run, application, revision, now, "active", "intent-recorded", now))
            for i in intents:
                self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (run, i.component, i.previous, i.target, i.runtime, "prepared", "intent-recorded", "unknown", 0, 0, now))
                self._event(run, i.component)

    def _event(self, run, component):
        self.db.execute("INSERT INTO events(run,component,state,reason,readiness,probes,consecutive,at) SELECT run,component,state,reason,readiness,probes,consecutive,updated FROM attempts WHERE run=? AND component=?", (run, component))

    def transition(self, run, component, state, reason, evidence=None):
        if (reason not in REASONS or state in ("recovered-verified", "contained") or
            reason in ("recovery-verified", "empty-bootstrap-contained")):
            raise JournalError("invalid-journal-reason")
        with self.transaction():
            row = self.db.execute("SELECT * FROM attempts WHERE run=? AND component=?", (run, component)).fetchone()
            if row is None or state not in TRANSITIONS[row["state"]]:
                raise JournalError("invalid-lifecycle-transition")
            readiness, probes, consecutive = ((evidence.status, evidence.probes, evidence.consecutive) if evidence else ("unknown", 0, 0))
            if (readiness not in ("unknown", "running", "ready", "failed", "verified") or
                type(probes) is not int or type(consecutive) is not int or not 0 <= consecutive <= probes <= 3001 or
                (state in ("succeeded", "rolled-back-verified") and (readiness != "verified" or consecutive < 2))):
                raise JournalError("invalid-readiness-evidence")
            self.db.execute("UPDATE attempts SET state=?,reason=?,readiness=?,probes=?,consecutive=?,updated=? WHERE run=? AND component=?",
                            (state, reason, readiness, probes, consecutive, time.time(), run, component))
            self._event(run, component)

    def promote(self, run):
        with self.transaction():
            rows = [r for r in self.rows() if r["run"] == run]
            if not rows or any(r["state"] != "succeeded" or r["readiness"] != "verified" or r["run_status"] != "active" for r in rows):
                raise JournalError("unverified-last-known-good")
            self.db.execute("UPDATE runs SET status='succeeded',reason='readiness-verified',updated=? WHERE id=?", (time.time(), run))
            for r in rows:
                self.db.execute("INSERT INTO good VALUES (?,?,?) ON CONFLICT(application,component) DO UPDATE SET run=excluded.run",
                                (r["application"], r["component"], run))

    def last_good(self, application, component):
        self._locked()
        row = self.db.execute("SELECT attempts.*, runs.revision FROM good JOIN attempts ON good.run=attempts.run AND good.component=attempts.component JOIN runs ON runs.id=attempts.run WHERE good.application=? AND good.component=?", (application, component)).fetchone()
        return dict(row) if row else None

    def recovery_baseline(self, run, *, completed=False):
        """Require the entire preceding accepted application, never a mixed LKG.

        Historical validation uses the preceding success, since later successful
        deployments may legitimately advance the current good pointers.
        """
        self._locked()
        rows = [r for r in self.rows() if r["run"] == run]
        if not rows or any(r["run_status"] != ("recovered" if completed else "active") for r in rows):
            raise JournalError("unverified-recovery")
        baseline = self.db.execute("SELECT id FROM runs WHERE application=? AND status='succeeded' AND rowid < (SELECT rowid FROM runs WHERE id=?) ORDER BY rowid DESC LIMIT 1",
                                   (rows[0]["application"], run)).fetchone()
        good = {r["component"]: r for r in self.rows() if baseline and r["run"] == baseline[0]}
        if set(good) != {r["component"] for r in rows}:
            raise JournalError("incomplete-recovery-baseline")
        restored = False
        for r in rows:
            events = self.db.execute("SELECT state FROM events WHERE run=? AND component=? ORDER BY sequence", (run, r["component"])).fetchall()
            state = events[-2][0] if completed and len(events) >= 2 else r["state"]
            g = good[r["component"]]
            if (state not in ("prepared", "succeeded", "rolled-back-verified") or
                r["previous"] != g["target"] or r["runtime"] != g["runtime"] or
                (state == "succeeded" and r["target"] != g["target"]) or
                (state == "rolled-back-verified" and r["target"] == g["target"])):
                raise JournalError("unverified-recovery")
            restored |= state == "rolled-back-verified"
            if not completed and self.last_good(r["application"], r["component"])["run"] != baseline[0]:
                raise JournalError("recovery-baseline-changed")
        if not restored:
            raise JournalError("unverified-recovery")
        return good

    def recover(self, run, evidence):
        """Close an in-process verified restoration without accepting its target.

        The trusted lifecycle supplies fresh whole-app runtime/ownership/gateway
        checks and readiness, under the same host lease. No post-crash API.
        """
        with self.transaction():
            if [r[0] for r in self.db.execute("PRAGMA quick_check")] != ["ok"]:
                raise JournalError("corrupt-journal")
            if self.db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise JournalError("corrupt-journal")
            self.validate()
            good = self.recovery_baseline(run)
            if set(evidence) != set(good) or any(
                e.status != "verified" or type(e.probes) is not int or type(e.consecutive) is not int or
                not 2 <= e.consecutive <= e.probes <= 3001 for e in evidence.values()
            ):
                raise JournalError("invalid-readiness-evidence")
            now = time.time()
            for name, e in evidence.items():
                self.db.execute("UPDATE attempts SET state='recovered-verified',reason='recovery-verified',readiness=?,probes=?,consecutive=?,updated=? WHERE run=? AND component=?",
                                (e.status, e.probes, e.consecutive, now, run, name))
                self._event(run, name)
            self.db.execute("UPDATE runs SET status='recovered',reason='recovery-verified',updated=? WHERE id=?", (now, run))
            self.validate()

    def fail_run(self, run, reason="recovery-analysis-required"):
        if reason not in REASONS:
            raise JournalError("invalid-journal-reason")
        with self.transaction():
            self.db.execute("UPDATE runs SET status='recovery-required',reason=?,updated=? WHERE id=? AND status='active'", (reason, time.time(), run))

    def incomplete(self):
        return [r for r in self.rows() if r["run_status"] not in ("succeeded", "recovered", "contained")]

    def bootstrap_containment_record(self, run, *, completed=False):
        """One failed initial bootstrap, never an application rollback or LKG.

        A contained record retains its original intent, target and consumed nonce.
        Only the separate operator-only administrative entry may close it after
        authenticating exact journal bytes and fresh complete empty-host facts.
        """
        self._locked()
        from scripts.production_integration.authorization import OperationCandidate
        rows = [r for r in self.rows() if r['run'] == run]
        approval = self.db.execute('SELECT * FROM authorizations WHERE nonce=?', (run,)).fetchone()
        if len(rows) != 1 or approval is None:
            raise JournalError('bootstrap-containment-not-eligible')
        row = rows[0]
        candidate = OperationCandidate(**json.loads(approval['payload']))
        events = [dict(e) for e in self.db.execute('SELECT * FROM events WHERE run=? ORDER BY sequence', (run,))]
        states = ['prepared', 'applying'] + (['contained'] if completed else [])
        reasons = ['intent-recorded', 'mutation-started'] + (['empty-bootstrap-contained'] if completed else [])
        if (candidate.scope != 'first-bootstrap' or candidate.identity != approval['candidate'] or
            row['application'] != 'manager-bootstrap' or row['component'] != 'gateway' or
            row['revision'] != candidate.manager_sha or row['target'] != candidate.image or
            row['runtime'] != candidate.runtime_hash.removeprefix('sha256:') or row['previous'] is not None or
            row['run_status'] != ('contained' if completed else 'recovery-required') or
            row['state'] != ('contained' if completed else 'applying') or
            row['run_reason'] != ('empty-bootstrap-contained' if completed else 'recovery-analysis-required') or
            approval['state'] != ('blocked' if completed else 'recovery-required') or
            [e['state'] for e in events] != states or [e['reason'] for e in events] != reasons or
            any(e['component'] != 'gateway' or e['readiness'] != 'unknown' or e['probes'] or e['consecutive'] for e in events) or
            (completed and row['run_updated'] != row['updated'])):
            raise JournalError('bootstrap-containment-not-eligible')
        return candidate

    def contain_empty_bootstrap(self, run, candidate):
        """Trusted administrative primitive, not an executor/request capability.

        Caller must durably retain the signed decision and original database and
        attest empty runtime under this same lease before entering this method.
        No tables, intents, events, approvals or LKG pointers are removed.
        """
        with self.transaction():
            self.validate()
            if (any(self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] != count
                    for table, count in (('runs', 1), ('attempts', 1), ('authorizations', 1), ('good', 0))) or
                self.bootstrap_containment_record(run).identity != candidate):
                raise JournalError('bootstrap-containment-not-eligible')
            now = time.time()
            self.db.execute("UPDATE attempts SET state='contained',reason='empty-bootstrap-contained',updated=? WHERE run=?", (now, run))
            self._event(run, 'gateway')
            self.db.execute("UPDATE runs SET status='contained',reason='empty-bootstrap-contained',updated=? WHERE id=?", (now, run))
            self.db.execute("UPDATE authorizations SET state='blocked' WHERE nonce=? AND state='recovery-required'", (run,))
            self.validate()

    def consume_authorization(self, claims, candidate):
        """Irreversible reservation, durable before intent or any host mutation.

        An interrupted reservation blocks even a different candidate/nonce. No
        reset/expiry/garbage collection can release an uncertain operation.
        """
        from dataclasses import asdict
        from scripts.reconciliation import encode
        if claims.candidate != candidate.identity:
            raise JournalError('authorization-candidate-mismatch')
        with self.transaction():
            if self.incomplete() or self.db.execute("SELECT 1 FROM authorizations WHERE state IN ('consumed','recovery-required')").fetchone():
                raise JournalError('production-recovery-analysis-required')
            if self.db.execute('SELECT count(*) FROM authorizations').fetchone()[0] >= 10000:
                raise JournalError('authorization-retention-required')
            self.db.execute('INSERT INTO authorizations VALUES (?,?,?,?,?,?,?)',
                            (claims.nonce, claims.candidate, claims.operator, claims.not_before, claims.expires, 'consumed', encode(asdict(candidate))))

    def production_bootstrap(self, host, daemon):
        """Journal success must match a consumed first-bootstrap authorization."""
        good = self.last_good('manager-bootstrap', 'gateway')
        if good is None:
            return False
        row = self.db.execute("SELECT payload FROM authorizations WHERE nonce=? AND state='succeeded'", (good['run'],)).fetchone()
        if row is None:
            return False
        c = json.loads(row[0])
        return c['scope'] == 'first-bootstrap' and (c['host'], c['daemon']) == (host, daemon)

    def authorization_recovery(self):
        """Analysis only, including death BEFORE an engine intent existed."""
        self._locked()
        return tuple({'nonce': r['nonce'], 'candidate': r['candidate'], 'state': r['state'],
                      'decision': 'recovery-required', 'reason': 'authorization-consumed-outcome-unverified'}
                     for r in self.db.execute("SELECT nonce,candidate,state FROM authorizations WHERE state IN ('consumed','recovery-required') ORDER BY nonce"))

    def finish_authorization(self, nonce, state):
        if state not in ('succeeded', 'blocked', 'recovery-required'):
            raise JournalError('invalid-authorization-state')
        with self.transaction():
            cursor = self.db.execute("UPDATE authorizations SET state=? WHERE nonce=? AND state='consumed'", (state, nonce))
            if cursor.rowcount != 1:
                raise JournalError('authorization-already-consumed')


@contextmanager
def locked_journal(directory, *, lock_directory="/tmp/portfolio-deployment-locks"):
    with host_lock(lock_directory) as lease:
        with Journal(directory, lease) as journal:
            yield journal
