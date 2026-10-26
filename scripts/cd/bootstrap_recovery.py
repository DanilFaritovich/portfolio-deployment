"""Operator-only completion of a failed bootstrap with already-created resources.

No Docker mutation or bootstrap replay. Retain the exact signed original journal
before recording newly verified readiness and the first truthful gateway LKG.
The existing journal schema and frozen Journal implementation remain unchanged.
"""
import base64
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

from scripts.cd.bootstrap_containment import BootstrapContainment, digest, journal_bytes, read_journal
from scripts.cd.signatures import authenticate
from scripts.deployment_journal import Journal, private_directory
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json
from scripts.readiness import ReadinessEvidence

NAMESPACE = 'portfolio-bootstrap-verification-recovery-v1'
FIELDS = {'version', 'id', 'run', 'candidate', 'host', 'daemon', 'manager_sha',
          'inventory_sha256', 'capsule_sha256', 'policy_id', 'journal_sha256',
          'observation_sha256', 'not_before', 'expires'}


def eligible(journal, run, *, completed=False):
    """Exactly one failed verifying bootstrap; prior contained attempts stay intact."""
    journal.validate()
    rows = journal.rows()
    require(1 <= len(rows) <= 16 and len({r['run'] for r in rows}) == len(rows) and
            journal.db.execute('SELECT count(*) FROM authorizations').fetchone()[0] == len(rows),
            'bootstrap-recovery-history-ineligible')
    selected = [r for r in rows if r['run'] == run]
    require(len(selected) == 1, 'bootstrap-recovery-run-ineligible')
    row = selected[0]
    approval = journal.db.execute('SELECT * FROM authorizations WHERE nonce=?', (run,)).fetchone()
    require(approval is not None, 'bootstrap-recovery-authorization-required')
    candidate = OperationCandidate(**json.loads(approval['payload']))
    events = [dict(e) for e in journal.db.execute('SELECT * FROM events WHERE run=? ORDER BY sequence', (run,))]
    states = ['prepared', 'applying', 'verifying'] + (['succeeded'] if completed else [])
    reasons = ['intent-recorded', 'mutation-started', 'mutation-completed'] + (['readiness-verified'] if completed else [])
    require(candidate.scope == 'first-bootstrap' and approval['candidate'] == candidate.identity and
            row['application'] == 'manager-bootstrap' and row['component'] == 'gateway' and
            row['revision'] == candidate.manager_sha and row['target'] == candidate.image and
            row['runtime'] == candidate.runtime_hash.removeprefix('sha256:') and row['previous'] is None and
            row['run_status'] == ('succeeded' if completed else 'recovery-required') and
            row['state'] == ('succeeded' if completed else 'verifying') and
            row['run_reason'] == ('readiness-verified' if completed else 'recovery-analysis-required') and
            approval['state'] == ('succeeded' if completed else 'recovery-required') and
            [e['state'] for e in events] == states and [e['reason'] for e in events] == reasons and
            all(e['component'] == 'gateway' and e['readiness'] == 'unknown' and
                e['probes'] == e['consecutive'] == 0 for e in events[:3]),
            'bootstrap-recovery-run-ineligible')
    for previous in rows:
        if previous['run'] == run:
            continue
        require(previous['run_status'] == 'contained' and previous['run_updated'] <= events[0]['at'],
                'bootstrap-recovery-history-ineligible')
        old = journal.bootstrap_containment_record(previous['run'], completed=True)
        require((old.host, old.daemon) == (candidate.host, candidate.daemon),
                'bootstrap-recovery-history-ineligible')
    good = [dict(r) for r in journal.db.execute('SELECT * FROM good')]
    require(good == ([{'application': 'manager-bootstrap', 'component': 'gateway', 'run': run}] if completed else []),
            'bootstrap-recovery-existing-lkg')
    return candidate


class BootstrapRecovery(BootstrapContainment):
    """Reuse exclusive audit retention, never empty-host containment eligibility."""
    audit_prefix = 'bootstrap-verification-recovery-'

    def __init__(self, *, observe, **kwargs):
        super().__init__(observe_empty=None, **kwargs)
        self.observe = observe

    def facts(self, journal, raw, run, *, completed=False):
        self.installation.verify()
        self.policy.current()
        candidate = eligible(journal, run, completed=completed)
        require((candidate.host, candidate.daemon, candidate.manager_sha) ==
                (self.policy.document['host'], self.policy.document['daemon'], self.installation.manager_sha),
                'bootstrap-recovery-installation-mismatch')
        return candidate, {'version': 1, 'run': run, 'candidate': candidate.identity,
            'host': candidate.host, 'daemon': candidate.daemon, 'manager_sha': candidate.manager_sha,
            'policy_id': self.policy.identity, 'journal_sha256': digest(raw), **self.inventories()}

    def probe(self, candidate, lease):
        observation, evidence = self.observe(candidate, lease)
        require(type(evidence) is ReadinessEvidence and evidence.status == 'verified' and
                evidence.reason == 'readiness-verified' and
                type(evidence.probes) is int and type(evidence.consecutive) is int and
                2 <= evidence.consecutive <= evidence.probes <= 3001,
                'bootstrap-recovery-readiness-unverified')
        return hashed(observation), evidence

    def prepare(self):
        with self.lock() as lease:
            with read_journal(self.directory, lease) as (journal, raw):
                pending = journal.incomplete()
                require(len(pending) == 1, 'bootstrap-recovery-single-run-required')
                candidate, facts = self.facts(journal, raw, pending[0]['run'])
                observation, _ = self.probe(candidate, lease)
            now = int(self.clock())
            return {'status': 'prepared', 'decision': {**facts, 'observation_sha256': observation,
                'id': uuid.uuid4().hex, 'not_before': now - 5, 'expires': now + 295},
                'executorStarted': False, 'bootstrapReplayed': False}

    def decision(self, envelope):
        decision, _ = authenticate(envelope, keys=self.keys, namespace=NAMESPACE)
        require(type(decision) is dict and set(decision) == FIELDS and type(decision['version']) is int and
                decision['version'] == 1 and
                all(type(decision[k]) is str and re.fullmatch('[0-9a-f]{32}', decision[k]) for k in ('id', 'run')) and
                all(type(decision[k]) is int for k in ('not_before', 'expires')) and
                0 <= decision['not_before'] <= self.clock() < decision['expires'] and
                0 < decision['expires'] - decision['not_before'] <= 300,
                'bootstrap-recovery-decision-invalid-or-expired')
        return decision

    def execute(self, envelope):
        decision = self.decision(envelope)
        with self.lock() as lease:
            with read_journal(self.directory, lease) as (journal, raw):
                selected = [r for r in journal.rows() if r['run'] == decision['run']]
                completed = len(selected) == 1 and selected[0]['run_status'] == 'succeeded'
                candidate, facts = self.facts(journal, raw, decision['run'], completed=completed)
                if completed:
                    # Verify retained private audit bytes before an idempotent reply.
                    target = self.state / (self.audit_prefix + decision['id'])
                    require(target.is_dir() and not target.is_symlink(), 'bootstrap-recovery-original-evidence-required')
                    retained = {}
                    with private_directory(target) as fd:
                        for name, limit in (('original.sqlite3', 16 * 1024 * 1024), ('approval.json', 131072)):
                            with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd), 'rb') as stream:
                                info = os.fstat(stream.fileno())
                                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                                        not info.st_mode & 0o077 and info.st_nlink == 1 and 0 < info.st_size <= limit,
                                        'bootstrap-recovery-original-evidence-required')
                                retained[name] = stream.read(limit + 1)
                                identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                                                      s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                                require(len(retained[name]) == info.st_size and
                                        identity(info) == identity(os.fstat(stream.fileno())) ==
                                        identity(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                                        'bootstrap-recovery-original-evidence-required')
                    original = retained['original.sqlite3']
                    require(retained['approval.json'] == envelope, 'bootstrap-recovery-original-evidence-required')
                    facts['journal_sha256'] = digest(original)
                require({k: decision[k] for k in facts} == facts, 'bootstrap-recovery-exact-decision-changed')
                observed, evidence = self.probe(candidate, lease)
                require(observed == decision['observation_sha256'], 'bootstrap-recovery-observation-changed')
            self.decision(envelope)
            self.installation.verify()
            self.policy.current()
            if not completed:
                self.retain(decision, envelope, raw)
                observed, evidence = self.probe(candidate, lease)
                require(observed == decision['observation_sha256'] and journal_bytes(self.directory) == raw,
                        'bootstrap-recovery-observation-changed')
                self.decision(envelope)
                self.policy.current()
                with Journal(self.directory, lease) as journal, journal.transaction():
                    fresh, facts = self.facts(journal, raw, decision['run'])
                    require(fresh == candidate and {k: decision[k] for k in facts} == facts,
                            'bootstrap-recovery-run-changed')
                    self.decision(envelope)
                    now = time.time()
                    journal.db.execute("UPDATE attempts SET state='succeeded',reason='readiness-verified',readiness=?,probes=?,consecutive=?,updated=? WHERE run=? AND component='gateway'",
                                       (evidence.status, evidence.probes, evidence.consecutive, now, decision['run']))
                    journal._event(decision['run'], 'gateway')
                    # Only this signed administrative primitive can terminalize a
                    # recovery-required bootstrap. Normal promotion stays fenced.
                    journal.db.execute("UPDATE runs SET status='succeeded',reason='readiness-verified',updated=? WHERE id=?",
                                       (now, decision['run']))
                    journal.db.execute('INSERT INTO good VALUES (?,?,?)',
                                       ('manager-bootstrap', 'gateway', decision['run']))
                    journal.db.execute("UPDATE authorizations SET state='succeeded' WHERE nonce=? AND state='recovery-required'",
                                       (decision['run'],))
                    journal.validate()
            return {'status': 'succeeded', 'run': decision['run'], 'candidate': candidate.identity,
                    'executorStarted': False, 'bootstrapReplayed': False, 'lastGoodCreated': not completed}


def main():
    require(os.geteuid() == 0, 'bootstrap-recovery-root-required')
    from scripts.cd.administrative import private, CAPSULE
    from scripts.cd.bridge import installation
    from scripts.cd.application import installed_policy
    from scripts.production_integration.fixed_bindings import build_fixed
    from scripts.production_integration.locking import production_host_lock, require_production_lease
    from scripts.production_integration.operator_approval import TRUST, trust_keys
    installed = installation()
    policy = installed_policy(installed)
    import cd_bindings
    def observe(candidate, lease):
        require_production_lease(lease)
        # Fixed reviewed wiring, never a request-selected runtime/adapter.
        executor = build_fixed(installed, cd_bindings.POLICY)
        port = executor.bootstrap.port
        return port.attestor.resources.verify_existing(candidate, port.caddy)
    operation = BootstrapRecovery(installation=installed, policy=policy, lock=production_host_lock,
        observe=observe, keys=trust_keys(private(TRUST, 8192)), inventories=lambda: {
            'inventory_sha256': digest(private(Path('/opt/portfolio-deployment/installation.json'), 2*1024*1024)),
            'capsule_sha256': digest(private(CAPSULE / 'capsule.json', 2*1024*1024))})
    request = closed_json(sys.stdin.buffer.read(131073), 131072)
    require(request == {'op': 'prepare'} or
            (type(request) is dict and set(request) == {'op', 'envelope'} and request['op'] == 'execute'),
            'bootstrap-recovery-operation-invalid')
    result = operation.prepare() if request['op'] == 'prepare' else operation.execute(
        base64.b64decode(request['envelope'], validate=True))
    print(canonical(result).decode())
    return 0
