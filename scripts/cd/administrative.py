"""Root-channel repair of a reviewed, never-bootstrapped empty installation.

The ordinary CD journal gate is unchanged. This entry requires independently
published exact bytes and an operator signature; delegated SSH cannot call it.
"""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from installation.operator_bootstrap import LIMIT, exclusive
from scripts.cd.policy import pin
from scripts.production_integration.authorization import OperationCandidate, review_approval
from scripts.production_integration.gateway_write import durable_replace
from scripts.production_integration.installation import protected
from scripts.production_integration.local_sources import ReadOnlyJournalSource
from scripts.production_integration.locking import require_production_lease
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import canonical, closed_json, authenticate_signed

BASE = Path('/var/lib/portfolio-deployment-bootstrap')
CAPSULE = Path('/usr/local/lib/portfolio-deployment-cd')
DECISION = BASE / 'prebootstrap-update.json'
BUNDLE = BASE / 'prebootstrap-manager.zip'
FIELDS = {'version', 'id', 'machine_id', 'daemon', 'from_sha', 'to_sha',
          'inventory_sha256', 'capsule_sha256', 'policy_id', 'bundle_sha256', 'not_before', 'expires'}


def private(path, limit):
    protected(path)
    info = path.stat()
    require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_gid == 0 and info.st_size <= limit,
            'cd-admin-file-invalid')
    raw = path.read_bytes()
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                          s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(len(raw) <= limit and identity(after) == identity(info), 'cd-admin-file-changed')
    return raw


class AdministrativeUpgrade:
    def __init__(self, manager, *, decision=DECISION, capsule=CAPSULE):
        self.manager, self.path, self.capsule = manager, Path(decision), Path(capsule)
        self.raw = private(self.path, 8192)
        self.document = closed_json(self.raw, 8192)
        self.active = False
        self.recovering = None
        self.record = None

    def current(self):
        d = self.document
        require(private(self.path, 8192) == self.raw and set(d) == FIELDS and
                type(d['version']) is int and d['version'] == 1, 'cd-admin-decision-invalid')
        require(type(d['id']) is str and re.fullmatch('[0-9a-f]{32}', d['id']) and
                all(type(d[k]) is str and re.fullmatch('[0-9a-f]{40}', d[k]) for k in ('from_sha', 'to_sha')) and
                all(type(d[k]) is str and re.fullmatch('[0-9a-f]{64}', d[k])
                    for k in ('inventory_sha256', 'capsule_sha256', 'bundle_sha256')),
                'cd-admin-decision-invalid')
        require(all(type(d[k]) is int for k in ('not_before', 'expires')) and
                d['not_before'] <= time.time() < d['expires'] and
                0 < d['expires'] - d['not_before'] <= 3600, 'cd-admin-decision-expired')
        m = self.manager
        m.policy.current()
        m.installation.verify()
        require(d['machine_id'] == m.policy.document['host'] and d['daemon'] == m.policy.document['daemon'] and
                d['policy_id'] == m.policy.identity and d['from_sha'] == m.installation.manager_sha and
                pin(private(m.root / 'installation.json', 2*1024*1024)) == d['inventory_sha256'] and
                pin(private(self.capsule / 'capsule.json', 2*1024*1024)) == d['capsule_sha256'],
                'cd-admin-installation-mismatch')
        return d

    def check(self, lease):
        """Reobserved under the existing flock before staging and exchange."""
        from scripts.cd.quiescence import initial_review
        require_production_lease(lease)
        d = self.current()
        m = self.manager
        marker = m.state / 'journal-established.json'
        require(not marker.exists() and not marker.is_symlink(), 'cd-journal-history-lost')
        require(ReadOnlyJournalSource().read(lease) == {'database': 'absent', 'recovery': 'unestablished'},
                'cd-admin-never-bootstrapped-host-required')
        receipts = list(m.state.glob('admin-upgrade-*.json'))
        require(len(receipts) <= 10000, 'cd-admin-retention-required')
        for path in receipts:
            r = closed_json(private(path, 131072), 131072)
            own = self.active and r['id'] == d['id'] and r['decision_sha256'] == pin(self.raw)
            recovering = self.recovering is not None and (r.get('upgrade_nonce'), r.get('candidate')) == self.recovering
            require(own or recovering or r['status'] in ('succeeded', 'rolled-back', 'recovered', 'no-op'),
                    'cd-admin-recovery-required')
            require(own or r['id'] != d['id'], 'cd-admin-decision-consumed')
        initial_review(m.installation, m.policy)
        require(ReadOnlyJournalSource().read(lease) == {'database': 'absent', 'recovery': 'unestablished'},
                'cd-admin-journal-changed')

    def prepare(self, raw):
        d = self.current()
        require(pin(raw) == d['bundle_sha256'], 'cd-admin-bundle-mismatch')
        with self.manager.lock() as lease:
            self.check(lease)
            candidate = self.manager.prepare(raw)
            require(candidate.manager_sha == d['to_sha'], 'cd-admin-target-mismatch')
            return candidate

    def reserve(self, nonce, *, candidate=None, original=None):
        d = self.current()
        self.record = {'id': d['id'], 'decision_sha256': pin(self.raw), 'upgrade_nonce': nonce,
                       'status': 'reserved', 'from_sha': d['from_sha'], 'to_sha': d['to_sha'],
                       'baseline_sha': d['from_sha']}
        if original is not None:
            self.record['original'] = json.loads(canonical(original))
            self.record['signature_nonce'] = nonce
            self.record['upgrade_nonce'] = original['upgrade_nonce']
            self.record['baseline_sha'] = original['baseline_sha']
        if candidate is not None:
            self.record['candidate'] = asdict(candidate)
        exclusive(self.manager.state / ('admin-upgrade-' + d['id'] + '.json'), canonical(self.record))
        self.active = True
        self.manager.administrative_operation = (d['id'], pin(self.raw))

    def finish(self, status):
        with self.manager.lock():
            self.record['status'] = status
            durable_replace(self.manager.state, 'admin-upgrade-' + self.document['id'] + '.json', canonical(self.record))

    def execute(self, raw, candidate, envelope):
        require(self.prepare(raw) == candidate, 'cd-admin-candidate-mismatch')
        claims = authenticate_signed(envelope, self.manager.policy.keys)
        # Validate the exact candidate and freshness before consuming root authority.
        class Verifier:
            def authenticate(_, value):
                return authenticate_signed(value, self.manager.policy.keys)
        review_approval(candidate, envelope, verifier=Verifier(), now=time.time())
        require(claims.expires - claims.not_before <= 300, 'cd-admin-signature-too-long')
        with self.manager.lock() as lease:
            self.check(lease)
            self.reserve(claims.nonce, candidate=candidate)
        try:
            result = self.manager.run_operator(raw, candidate, envelope)
        except BaseException:
            self.finish('recovery-required')
            raise
        self.finish(result['status'])
        return result

    def recover(self, record_id, envelope):
        """Fresh root decision + operator signature; restore, never replay upgrade."""
        d = self.current()
        require(type(record_id) is str and re.fullmatch('[0-9a-f]{32}', record_id), 'cd-admin-recovery-record-invalid')
        path = self.manager.state / ('admin-upgrade-' + record_id + '.json')
        original = closed_json(private(path, 131072), 131072)
        require(original['id'] == record_id and 'candidate' in original, 'cd-admin-recovery-record-invalid')
        nonce = original['upgrade_nonce']
        candidate = OperationCandidate(**original['candidate'])
        require(candidate.manifest_hash == 'sha256:' + d['bundle_sha256'], 'cd-admin-recovery-target-mismatch')
        class Verifier:
            def authenticate(_, value):
                return authenticate_signed(value, self.manager.policy.keys)
        claims = review_approval(candidate, envelope, verifier=Verifier(), now=time.time())
        require(claims.expires - claims.not_before <= 300, 'cd-admin-signature-too-long')
        self.recovering = (nonce, original['candidate'])
        with self.manager.lock() as lease:
            self.check(lease)
            self.reserve(claims.nonce, candidate=candidate, original=original)
        try:
            receipt = self.manager.state / ('upgrade-' + nonce + '.json')
            if receipt.exists():
                from scripts.planning_inputs import parse_snapshot
                record = parse_snapshot(private(receipt, 2*1024*1024))
                require(record['candidate'] == original['candidate'], 'cd-admin-recovery-candidate-mismatch')
                if record['status'] in ('succeeded', 'no-op'):
                    require(d['to_sha'] == candidate.manager_sha and
                            self.manager.installation.manager_sha == d['to_sha'] and
                            self.manager.health(self.manager.root, d['to_sha']) is True,
                            'cd-admin-recovery-health-unverified')
                    result = {'status': 'succeeded', 'manager_sha': d['to_sha']}
                elif record['status'] != 'rolled-back':
                    require(d['to_sha'] == original['baseline_sha'], 'cd-admin-recovery-target-mismatch')
                    result = self.manager.recover(nonce, envelope)
                else:
                    require(d['to_sha'] == original['baseline_sha'] and
                            self.manager.installation.manager_sha == d['to_sha'] and
                            self.manager.health(self.manager.root, d['to_sha']) is True,
                            'cd-admin-recovery-health-unverified')
                    result = {'status': 'rolled-back', 'manager_sha': d['to_sha']}
            else:
                with self.manager.lock() as lease:
                    self.check(lease)
                    require(d['to_sha'] == original['baseline_sha'] and
                            self.manager.installation.manager_sha == d['to_sha'] and
                            self.manager.health(self.manager.root, d['to_sha']) is True,
                            'cd-admin-recovery-health-unverified')
                result = {'status': 'rolled-back', 'manager_sha': d['to_sha']}
            with self.manager.lock():
                for prior in self.manager.state.glob('admin-upgrade-*.json'):
                    row = closed_json(private(prior, 131072), 131072)
                    if row['id'] != d['id'] and (row.get('upgrade_nonce'), row.get('candidate')) == self.recovering:
                        row['status'] = 'recovered'
                        row['recovery_decision'] = d['id']
                        durable_replace(self.manager.state, prior.name, canonical(row))
            self.finish('succeeded')
            return result
        except BaseException:
            self.finish('recovery-required')
            raise


def main():
    from scripts.cd.bridge import installation, installed_policy, updater
    require(os.geteuid() == 0, 'cd-admin-root-required')
    request = closed_json(sys.stdin.buffer.read(131073), 131072)
    require(type(request) is dict and request.get('op') in ('prepare', 'execute', 'recover'), 'cd-admin-operation-invalid')
    expected = {'prepare': {'op'}, 'execute': {'op', 'candidate', 'envelope'},
                'recover': {'op', 'record_id', 'envelope'}}[request['op']]
    require(set(request) == expected, 'cd-admin-operation-invalid')
    seal = installation()
    manager = updater(seal, installed_policy(seal))
    admin = AdministrativeUpgrade(manager)
    manager.quiescent = admin.check
    if request['op'] == 'recover':
        result = admin.recover(request['record_id'], base64.b64decode(request['envelope'], validate=True))
    elif request['op'] == 'prepare':
        raw = private(BUNDLE, LIMIT)
        result = {'status': 'prepared', 'candidate': asdict(admin.prepare(raw))}
    else:
        raw = private(BUNDLE, LIMIT)
        result = admin.execute(raw, OperationCandidate(**request['candidate']),
                               base64.b64decode(request['envelope'], validate=True))
    print(canonical(result).decode())
    return 0 if result['status'] in ('prepared', 'succeeded', 'no-op') else 2
