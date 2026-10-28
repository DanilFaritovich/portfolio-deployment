"""Root-only verification recovery of an applied stateless image update.

No executor, Docker mutation, gateway activation, journal reset or replay.
Installed wiring authenticates the original retained release request independently.
"""
from dataclasses import asdict
import json
from pathlib import Path
import re
import time
import uuid

from scripts.cd.bootstrap_containment import BootstrapContainment, digest, read_journal, journal_bytes
from scripts.cd.signatures import authenticate
from scripts.deployment_journal import Journal
from scripts.deployment_lifecycle import runtime_identity
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical

NAMESPACE = 'portfolio-application-update-recovery-v1'
FACTS = {'version', 'scope', 'run', 'candidate', 'host', 'daemon', 'manager_sha',
         'image', 'previous_run', 'previous_image', 'policy_id', 'journal_sha256',
         'inventory_sha256', 'capsule_sha256', 'request_sha256', 'release_sha256',
         'current_release_sha256', 'observation_sha256'}
FIELDS = FACTS | {'id', 'not_before', 'expires'}


def eligible(journal, run, app):
    """Only a completed mutation followed by failed fresh-state verification."""
    rows = journal.rows()
    authorizations = journal.authorization_recovery()
    require(len(authorizations) == 1 and authorizations[0]['nonce'] == run,
            'update-recovery-single-pending-authorization-required')
    pending = journal.incomplete()
    require(len(pending) == 1 and pending[0]['run'] == run,
            'update-recovery-single-pending-run-required')
    selected = [r for r in rows if r['run'] == run]
    require(len(selected) == 1, 'update-recovery-single-component-required')
    row = selected[0]
    auth = journal.db.execute('SELECT * FROM authorizations WHERE nonce=?', (run,)).fetchone()
    require(auth is not None, 'update-recovery-authorization-required')
    candidate = OperationCandidate(**json.loads(auth['payload']))
    candidate.validate()
    require(candidate.scope == 'update-project-router' and auth['candidate'] == candidate.identity
            and auth['state'] == 'recovery-required'
            and row['application'] == app.id == 'project-router'
            and row['component'] == 'web' and len(app.spec['components']) == 1
            and app.spec['components'][0]['name'] == 'web'
            and app.spec['storage']['type'] == 'stateless'
            and not app.spec['configRefs'] and not app.spec['secretRefs']
            and all(not c['configRefs'] and not c['secretRefs'] for c in app.spec['components'])
            and row['target'] == app.spec['components'][0]['image'] == candidate.image
            and row['runtime'] == runtime_identity(app)
            and row['revision'] == candidate.manager_sha
            and row['run_status'] == 'recovery-required'
            and row['run_reason'] == 'recovery-analysis-required'
            and row['state'] == 'recovery-required' and row['reason'] == 'fresh-state-unavailable'
            and row['readiness'] == 'unknown', 'update-recovery-run-ineligible')
    events = [dict(e) for e in journal.db.execute(
        'SELECT * FROM events WHERE run=? ORDER BY sequence', (run,))]
    require([(e['component'], e['state'], e['reason']) for e in events] == [
        ('web', 'prepared', 'intent-recorded'), ('web', 'applying', 'mutation-started'),
        ('web', 'verifying', 'mutation-completed'),
        ('web', 'recovery-required', 'fresh-state-unavailable')], 'update-recovery-history-invalid')
    good = journal.last_good('project-router', 'web')
    require(good is not None and good['target'] == row['previous'] and row['previous'] != row['target']
            and good['runtime'] == row['runtime'] and good['state'] == 'succeeded',
            'update-recovery-previous-lkg-required')
    good_run = journal.db.execute(
        "SELECT g.run FROM good g WHERE application='project-router' AND component='web'").fetchone()[0]
    require(good_run != run, 'update-recovery-already-completed')
    return candidate, good_run, row['previous']


class UpdateRecovery(BootstrapContainment):
    """Dependencies originate in independently reviewed root-only wiring."""
    audit_prefix = 'application-update-recovery-'

    def __init__(self, *, installation, policy, lock, release_reader, application_reader,
                 observe, inventories, current_reader, current_writer, keys,
                 directory='/srv/portfolio/journal', state='/var/lib/portfolio-deployment-cd',
                 clock=time.time):
        self.installation, self.policy, self.lock = installation, policy, lock
        self.release_reader, self.application_reader = release_reader, application_reader
        self.observe, self.inventories = observe, inventories
        self.current_reader, self.current_writer = current_reader, current_writer
        self.keys, self.clock = keys, clock
        self.directory, self.state = Path(directory), Path(state)

    def _facts(self, lease):
        self.installation.verify()
        self.policy.current()
        with read_journal(self.directory, lease) as (journal, raw):
            pending = journal.incomplete()
            require(len(pending) == 1, 'update-recovery-single-pending-run-required')
            run = pending[0]['run']
            release, request_hash = self.release_reader(journal, run)
            app = self.application_reader(release)
            candidate, previous_run, previous_image = eligible(journal, run, app)
            require(candidate.manager_sha == self.installation.manager_sha
                    and (candidate.host, candidate.daemon) ==
                    (self.policy.document['host'], self.policy.document['daemon'])
                    and (candidate.image, candidate.producer_sha) == (release.image, release.source_sha),
                    'update-recovery-release-unbound')
            current = self.current_reader()
            if current is None:
                require(previous_image == self.policy.document['application']['initial_release']['image'],
                        'update-recovery-current-release-missing')
            else:
                from scripts.cd.release import ImageRelease
                saved = ImageRelease(**json.loads(current)).validate()
                require(canonical(asdict(saved)) == current and
                        (saved == release or saved.image == previous_image),
                        'update-recovery-current-release-conflict')
            inventories = self.inventories()
            require(set(inventories) == {'inventory_sha256', 'capsule_sha256'} and
                    all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
                        for v in (*inventories.values(), request_hash)),
                    'update-recovery-inventory-invalid')
            observation = self.observe(candidate, app, release, lease)
            require(type(observation) is dict
                    and set(observation) == {'application','component','revision','image',
                        'runtime','route','readiness','container_id','probes','consecutive'} and observation.get('application') == app.id
                    and observation.get('component') == 'web'
                    and observation.get('revision') == candidate.manager_sha
                    and observation.get('image') == candidate.image
                    and observation.get('runtime') == runtime_identity(app)
                    and observation.get('route') == app.spec['ingress']
                    and observation.get('readiness') == 'verified'
                    and type(observation.get('container_id')) is str
                    and re.fullmatch('[0-9a-f]{64}', observation['container_id'])
                    and type(observation.get('probes')) is int
                    and type(observation.get('consecutive')) is int
                    and 2 <= observation['consecutive'] <= observation['probes'] <= 3001,
                    'update-recovery-observation-invalid')
            return {'version': 1, 'scope': 'update-project-router', 'run': run,
                    'candidate': candidate.identity, 'host': candidate.host, 'daemon': candidate.daemon,
                    'manager_sha': candidate.manager_sha, 'image': candidate.image,
                    'previous_run': previous_run, 'previous_image': previous_image,
                    'policy_id': self.policy.identity, 'journal_sha256': digest(raw),
                    'request_sha256': request_hash, 'release_sha256': hashed(asdict(release)),
                    'current_release_sha256': None if current is None else digest(current),
                    'observation_sha256': hashed(observation), **inventories}, release, app, observation

    def prepare(self):
        with self.lock() as lease:
            facts, _, _, _ = self._facts(lease)
        now = int(self.clock())
        return {'status': 'prepared', 'decision': {**facts, 'id': uuid.uuid4().hex,
                'not_before': now - 5, 'expires': now + 895}, 'executorStarted': False}

    def decision(self, envelope):
        d, _ = authenticate(envelope, keys=self.keys, namespace=NAMESPACE)
        require(type(d) is dict and set(d) == FIELDS and type(d['version']) is int
                and d['version'] == 1 and d['scope'] == 'update-project-router'
                and type(d['id']) is str and re.fullmatch('[0-9a-f]{32}', d['id'])
                and type(d['not_before']) is int and type(d['expires']) is int
                and d['not_before'] <= self.clock() < d['expires']
                and 0 < d['expires'] - d['not_before'] <= 900,
                'update-recovery-decision-invalid-or-expired')
        return d

    def execute(self, envelope):
        decision = self.decision(envelope)
        with self.lock() as lease:
            facts, release, app, observed = self._facts(lease)
            require(facts == {k: decision[k] for k in FACTS}, 'update-recovery-state-changed')
            self.decision(envelope)
            with read_journal(self.directory, lease) as (_, raw):
                require(digest(raw) == decision['journal_sha256'], 'update-recovery-journal-changed')
                audit = self.retain(decision, envelope, raw)
            before = self.current_reader()
            require((None if before is None else digest(before)) == decision['current_release_sha256'],
                    'update-recovery-current-release-changed')
            original = canonical({'absent': True}) if before is None else before
            path = audit / 'previous-release.json'
            from installation.operator_bootstrap import exclusive, sync_directory
            require(not path.exists(), 'update-recovery-replay-forbidden')
            exclusive(path, original)
            sync_directory(audit)
            with Journal(self.directory, lease) as journal:
                with journal.transaction():
                    eligible(journal, decision['run'], app)
                    self.installation.verify()
                    self.policy.current()
                    self.decision(envelope)
                    require(digest(journal_bytes(self.directory)) == decision['journal_sha256'],
                            'update-recovery-journal-changed')
                    require(self.inventories() == {k: decision[k] for k in
                            ('inventory_sha256','capsule_sha256')}, 'update-recovery-inventory-changed')
                    require(self.current_reader() == before, 'update-recovery-current-release-changed')
                    # Publish before SQLite promotion. An interruption here leaves the
                    # original pending run fencing all ordinary delivery. A fresh
                    # prepare/signature may verify that exact release; no replay.
                    self.current_writer(canonical(asdict(release)))
                    require(self.current_reader() == canonical(asdict(release)),
                            'update-recovery-current-release-unverified')
                    now, run = self.clock(), decision['run']
                    journal.db.execute("UPDATE attempts SET state='succeeded',reason='readiness-verified',"
                        "readiness='verified',probes=?,consecutive=?,updated=? WHERE run=? AND component='web'",
                        (observed['probes'], observed['consecutive'], now, run))
                    # The terminal diagnostic cannot be followed by success in
                    # the frozen v2 validator. Retain its complete original DB
                    # and signed decision in audit, then repair only that final
                    # acceptance record. Every earlier event remains identical.
                    result = journal.db.execute(
                        "UPDATE events SET state='succeeded',reason='readiness-verified',"
                        "readiness='verified',probes=?,consecutive=?,at=? "
                        "WHERE run=? AND component='web' AND state='recovery-required' "
                        "AND reason='fresh-state-unavailable'",
                        (observed['probes'], observed['consecutive'], now, run))
                    require(result.rowcount == 1, 'update-recovery-terminal-event-changed')
                    journal.db.execute("UPDATE runs SET status='succeeded',reason='readiness-verified',updated=? WHERE id=?", (now, run))
                    journal.db.execute("UPDATE authorizations SET state='succeeded' WHERE nonce=?", (run,))
                    result = journal.db.execute("UPDATE good SET run=? WHERE application='project-router' "
                        "AND component='web' AND run=?", (run, decision['previous_run']))
                    require(result.rowcount == 1, 'update-recovery-lkg-changed')
                    journal.validate()
            return {'status': 'succeeded', 'run': run, 'candidate': decision['candidate'],
                    'image': release.image, 'executorStarted': False, 'mutationReplayed': False}
