"""Fixed-tree sealed upgrades, independent of the Manager being replaced.

Install this complete verifier capsule separately. It never imports candidate
code before independent review. Its filesystem receipts deliberately stay outside
the application journal: the old Manager must still read its unchanged schema
after rollback. The same canonical flock serializes both kinds of operation.
"""
from dataclasses import asdict
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from installation.operator_bootstrap import read_bundle, exclusive, sync_directory
from scripts.cd.policy import pin
from scripts.cd.policy import FROZEN_BOUNDARY
from scripts.production_integration.installation import protected
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import authenticate_signed
from scripts.production_integration.gateway_write import durable_replace

ROOT = Path('/opt/portfolio-deployment')
STATE = Path('/var/lib/portfolio-deployment-cd')


def exchange(left, right):
    """Linux same-filesystem atomic directory exchange; no symlink activation."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        raise OSError(ctypes.get_errno(), 'cd-directory-exchange-failed')


def verify_tree(root, *, active_root=ROOT, uid=0):
    """Check staged/retained bytes without importing them or following links."""
    root, active_root = Path(root), Path(active_root)
    document = json.loads((root / 'installation.json').read_bytes())
    require(set(document) == {'manager_sha', 'files'} and re.fullmatch(r'[0-9a-f]{40}', document['manager_sha']),
            'cd-installation-inventory-invalid')
    pins = dict(document['files'])
    require(len(pins) == len(document['files']) and bool(pins), 'cd-installation-inventory-invalid')
    actual, pending = set(), [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(info.st_uid == uid and not info.st_mode & 0o077 and
                (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'cd-tree-metadata-invalid')
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        else:
            name = str(active_root / path.relative_to(root))
            actual.add(name)
            if path.name != 'installation.json' or path.parent != root:
                require(name in pins and pin(path.read_bytes()) == pins[name], 'cd-tree-content-changed')
        require(len(actual) + len(pending) <= 8192, 'cd-tree-inventory-limit')
    internal = {p for p in pins if Path(p).is_relative_to(active_root)}
    require(actual == internal | {str(active_root / 'installation.json')}, 'cd-tree-inventory-mismatch')
    return document


class ManagerUpdater:
    def __init__(self, *, policy, bundle_verifier, installation, lock, quiescent, health,
                 root=ROOT, state=STATE, swap=exchange):
        self.policy, self.verifier, self.installation = policy, bundle_verifier, installation
        self.lock, self.quiescent, self.health = lock, quiescent, health
        self.root, self.state, self.swap = Path(root), Path(state), swap

    def fenced(self):
        administrative = list(self.state.glob('admin-upgrade-*.json'))
        require(len(administrative) <= 10000, 'cd-upgrade-retention-required')
        for path in administrative:
            protected(path)
            require(path.stat().st_size <= 131072, 'cd-admin-receipt-too-large')
            record = json.loads(path.read_bytes())
            own = getattr(self, 'administrative_operation', None) == (record['id'], record['decision_sha256'])
            require(own or record['status'] in ('succeeded', 'rolled-back', 'no-op', 'recovered'),
                    'cd-admin-recovery-required')
        receipts = list(self.state.glob('upgrade-*.json'))
        require(len(receipts) <= 10000, 'cd-upgrade-retention-required')
        for path in receipts:
            require(json.loads(path.read_bytes())['status'] in ('succeeded', 'rolled-back', 'no-op'),
                    'cd-manager-recovery-required')

    def prepare(self, raw):
        self.policy.current()
        self.installation.verify()
        self.fenced()
        proof = self.verifier.verify(raw)
        manifest, files = read_bundle(raw)
        p = self.policy.document['manager']
        frozen=FROZEN_BOUNDARY|{n for n in files if n.startswith(('schemas/','manager/'))}
        require(frozen <= p['frozen'].keys(),'cd-frozen-contract-policy-incomplete')
        require(proof.manager_sha == manifest['manager_sha'] and proof.bundle_sha256 == pin(raw), 'cd-bundle-proof-unbound')
        require(all(n in files and pin(files[n][0]) == digest for n, digest in p['frozen'].items()),
                'cd-frozen-manager-contract-changed')
        # Dependencies, tools, administrative binding and unit changes are manual.
        require({n: pin(v[0]) for n, v in files.items() if n.startswith('vendor/') or n == 'tools/caddy'} == p['assets'],
                'cd-dependencies-changed')
        old = verify_tree(self.root, active_root=self.root, uid=os.geteuid())
        require(old['manager_sha'] == self.installation.manager_sha, 'cd-installed-source-unbound')
        version=old['manager_sha']
        receipts=self.recovery_report()
        seen=set()
        while version!=self.policy.document['initial_manager_sha']:
            require(version not in seen,'cd-installed-lineage-invalid')
            seen.add(version)
            links=[r for r in receipts if r['status']=='succeeded' and r['candidate']['manager_sha']==version]
            require(len(links)==1,'cd-installed-lineage-unverified')
            version=links[0]['from']
        self.verifier.github.get('DanilFaritovich/portfolio-deployment', '')
        comparison = self.verifier.github.get('DanilFaritovich/portfolio-deployment',
            '/compare/' + old['manager_sha'] + '...' + proof.manager_sha)
        require(comparison['status'] in ('ahead', 'identical') and
                comparison['merge_base_commit']['sha'] == old['manager_sha'] and
                comparison['base_commit']['sha'] == old['manager_sha'], 'cd-manager-downgrade-forbidden')
        initial = self.policy.document['application']['initial_release']
        candidate = OperationCandidate(self.policy.document['host'], self.policy.document['daemon'], proof.manager_sha,
            initial['source_sha'], initial['image'], 'sha256:' + pin(raw), self.policy.identity,
            hashed({'from': old['manager_sha'], 'to': proof.manager_sha}), hashed(p['frozen']), hashed(p['assets']),
            hashed({'old_inventory': old}), hashed({'root': str(self.root)}), hashed({'schema': 2}), 'manager-upgrade')
        candidate.validate()
        return candidate

    def run(self, raw, candidate, envelope):
        role = self.policy.document['automation']
        return self._run(raw, candidate, envelope, {role['principal']: role['key']})

    def run_operator(self, raw, candidate, envelope):
        """Independent administrative entry only; no delegated SSH route calls this."""
        return self._run(raw, candidate, envelope, self.policy.keys)

    def _run(self, raw, candidate, envelope, keys):
        require(candidate.scope == 'manager-upgrade', 'cd-upgrade-scope-required')
        claims = authenticate_signed(envelope, keys)
        from scripts.production_integration.authorization import review_approval
        class Verifier:
            def authenticate(_, raw):
                return authenticate_signed(raw, keys)
        import time
        review_approval(candidate, envelope, verifier=Verifier(), now=time.time())
        require(claims.expires - claims.not_before <= 300, 'cd-upgrade-approval-too-long')
        with self.lock() as lease:
            self.quiescent(lease)
            require(self.prepare(raw) == candidate, 'cd-upgrade-candidate-changed')
            review_approval(candidate, envelope, verifier=Verifier(), now=time.time())
            name = 'upgrade-' + claims.nonce + '.json'
            receipt = self.state / name
            # O_EXCL is the replay reservation. Never expire/delete failed evidence.
            record = {'candidate': asdict(candidate), 'from': self.installation.manager_sha,
                      'status': 'reserved', 'retained': '.portfolio-deployment-' + claims.nonce,
                      'old_inventory': verify_tree(self.root, active_root=self.root, uid=os.geteuid())}
            exclusive(receipt, json.dumps(record, sort_keys=True).encode())
            if candidate.manager_sha == self.installation.manager_sha:
                require(self.health(self.root, candidate.manager_sha) is True, 'cd-manager-health-failed')
                record['status'] = 'no-op'
                durable_replace(self.state, name, json.dumps(record).encode())
                return {'status': 'no-op', 'manager_sha': candidate.manager_sha}
            manifest, files = read_bundle(raw)
            staged = self.root.parent / record['retained']
            staged.mkdir(mode=0o700)
            old_inventory = verify_tree(self.root, active_root=self.root, uid=os.geteuid())
            external = {p: h for p, h in old_inventory['files'] if not Path(p).is_relative_to(self.root)}
            require(external.get('/etc/systemd/system/portfolio-deployment-executor.service') == pin(files['executor.service'][0]),
                    'cd-unit-change-forbidden')
            inventory = dict(external)
            for n, (data, mode) in sorted(files.items()):
                if n == 'executor.service':
                    continue
                target = staged / n
                for parent in reversed(target.relative_to(staged).parents):
                    (staged / parent).mkdir(mode=0o700, exist_ok=True)
                exclusive(target, data, mode)
                inventory[str(self.root / n)] = pin(data)
            exclusive(staged / 'installation.json', json.dumps({'manager_sha': manifest['manager_sha'],
                      'files': sorted(map(list, inventory.items()))}, sort_keys=True, separators=(',', ':')).encode())
            verify_tree(staged, active_root=self.root, uid=os.geteuid())
            sync_directory(staged)
            record['new_inventory'] = verify_tree(staged, active_root=self.root, uid=os.geteuid())
            self.policy.current()
            self.installation.verify()
            self.quiescent(lease)
            review_approval(candidate,envelope,verifier=Verifier(),now=time.time())
            record['status'] = 'switch-intent'
            durable_replace(self.state, name, json.dumps(record).encode())
            # A crash/exception from here is UNKNOWN until independently inspected.
            self.swap(self.root, staged)
            sync_directory(self.root.parent)
            record['status'] = 'verifying'
            durable_replace(self.state, name, json.dumps(record).encode())
            healthy = self.health(self.root, candidate.manager_sha)
            if healthy is True:
                verify_tree(self.root, active_root=self.root, uid=os.geteuid())
                record['status'] = 'succeeded'
            elif healthy is False:
                # A defined failed check is reversible; exceptions/timeouts aren't.
                retained = verify_tree(staged, active_root=self.root, uid=os.geteuid())
                require(retained == old_inventory, 'cd-rollback-tree-changed')
                self.swap(self.root, staged)
                sync_directory(self.root.parent)
                require(self.health(self.root, old_inventory['manager_sha']) is True, 'cd-manager-rollback-unverified')
                record['status'] = 'rolled-back'
            else:
                raise RuntimeError('cd-manager-health-unknown')
            durable_replace(self.state, name, json.dumps(record).encode())
            return {'status': record['status'], 'manager_sha': (old_inventory['manager_sha'] if record['status']=='rolled-back'
                     else candidate.manager_sha), 'retained': str(staged)}

    def recovery_report(self):
        """Read-only. Never guesses completion or retries a started update."""
        return [json.loads(p.read_bytes()) for p in sorted(self.state.glob('upgrade-*.json'))]

    def recover(self, nonce, envelope):
        """Separately operator-signed, bounded restoration; never replay upgrade.

        Automation cannot invoke this method through its SSH role. Evidence and
        both trees remain. UNKNOWN is resolved only after exact tree comparison.
        """
        require(re.fullmatch(r'[0-9a-f]{32}', nonce), 'cd-recovery-nonce-invalid')
        from scripts.production_integration.authorization import review_approval
        import time
        with self.lock() as lease:
            self.quiescent(lease)
            self.policy.current()
            path = self.state / ('upgrade-' + nonce + '.json')
            record = json.loads(path.read_bytes())
            require(record['status'] not in ('succeeded', 'rolled-back', 'no-op'), 'cd-recovery-not-required')
            candidate = OperationCandidate(**record['candidate'])
            keys = self.policy.keys
            class Verifier:
                def authenticate(_, raw):
                    return authenticate_signed(raw, keys)
            claims = review_approval(candidate, envelope, verifier=Verifier(), now=time.time())
            exclusive(self.state / ('recovery-' + claims.nonce + '.json'), envelope)
            old = record['old_inventory']
            require(hashed({'old_inventory': old}) == candidate.network_hash, 'cd-recovery-baseline-unbound')
            active = verify_tree(self.root, active_root=self.root, uid=os.geteuid())
            if active != old:
                retained = self.root.parent / record['retained']
                require(verify_tree(retained, active_root=self.root, uid=os.geteuid()) == old and
                        active == record.get('new_inventory') and active['manager_sha'] == candidate.manager_sha,
                        'cd-recovery-tree-unknown')
                self.swap(self.root, retained)
                sync_directory(self.root.parent)
            require(verify_tree(self.root, active_root=self.root, uid=os.geteuid()) == old and
                    self.health(self.root, old['manager_sha']) is True, 'cd-recovery-restoration-unverified')
            record['status'] = 'rolled-back'
            record['recovery_nonce'] = claims.nonce
            durable_replace(self.state, path.name, json.dumps(record).encode())
            return {'status': 'rolled-back', 'manager_sha': old['manager_sha']}
