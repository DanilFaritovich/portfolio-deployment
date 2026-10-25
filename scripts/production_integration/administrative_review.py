"""Narrow root-channel evidence for the first installation's two phases.

Only a separately authenticated administrator publishes REVIEW. It is authority
data, never an inspection report, request-selected policy or executable module.
No writer/issuer is provided. Both shipped bindings continue to raise.
"""
from dataclasses import asdict, fields
import os
from pathlib import Path
import re
import stat
import time

from scripts.docker_discovery import ImageEvidence
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.candidate import hashed
from scripts.production_integration.host_policy import HostPolicy
from scripts.production_integration.installation import SealedInstallation, protected
from scripts.production_integration.observations import require
from scripts.reconciliation import encode

REVIEW = '/etc/portfolio-deployment/phase-review.json'
LIMIT = 65536
FIELDS = {'version', 'phase', 'manager_sha', 'host_policy', 'not_before', 'expires',
          'administrator', 'decision_id', 'caddy_runtime_hash', 'caddy_evidence_hash',
          'runtime_acceptance', 'rollout'}


def _file_identity(info):
    # Reading can update atime. Changes to ownership, permissions, links,
    # contents or the path binding must still invalidate this exact read.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_review():
    """Protected, bounded, stable read. Root ownership authenticates admin intake.

    The administrative channel MUST review and explicitly install these bytes.
    Hashes/reference strings on their own never authenticate their origin.
    """
    require(os.geteuid() == 0, 'production-review-root-context-required')
    protected(REVIEW)
    with open(REVIEW, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600 and
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= LIMIT,
                'production-review-metadata-invalid')
        raw = stream.read(LIMIT + 1)
        require(0 < len(raw) <= LIMIT and
                _file_identity(os.fstat(stream.fileno())) == _file_identity(info) and
                _file_identity(Path(REVIEW).lstat()) == _file_identity(info),
                'production-review-changed')
    value = parse_snapshot(raw)
    require(encode(value).encode() == raw, 'production-review-noncanonical')
    return value


class AdministrativePhaseReview:
    """Prerequisite + exact-Caddy-acceptance ports; no operation approval.

    Root is already the trusted administrative boundary. Expiry or removing/
    replacing REVIEW revokes every existing object at its next verification.
    It is intentionally data outside SealedInstallation's executable inventory:
    approved post-install inodes and post-bootstrap IDs do not exist at build time.
    The reader and phase composition code themselves MUST be sealed/pinned.
    """
    def __init__(self, installation):
        require(type(installation) is SealedInstallation, 'production-review-seal-required')
        self.installation = installation
        installation.verify()
        self.document = read_review()
        self._validate()
        self.policy = HostPolicy(**self.document['host_policy'])

    def _validate(self):
        d = self.document
        require(type(d) is dict and set(d) == FIELDS and type(d['version']) is int and d['version'] == 1,
                'production-review-fields-invalid')
        require(d['phase'] in ('empty-host', 'rollout') and d['manager_sha'] == self.installation.manager_sha,
                'production-review-phase-or-source-invalid')
        require(type(d['host_policy']) is dict and
                set(d['host_policy']) == {f.name for f in fields(HostPolicy)}, 'production-review-policy-invalid')
        p = HostPolicy(**d['host_policy'])
        require(type(p.host) is str and re.fullmatch(r'[0-9a-f]{32}', p.host) and
                type(p.daemon) is str and 0 < len(p.daemon) <= 256 and
                type(p.minimum_bytes) is int and p.minimum_bytes >= 1024**3 and
                type(p.minimum_inodes) is int and p.minimum_inodes > 0,
                'production-review-policy-invalid')
        for key in ('network_hash', 'resources_hash', 'storage_hash'):
            require(type(getattr(p, key)) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', getattr(p, key)),
                    'production-review-policy-invalid')
        for key in ('backup_restore', 'console_recovery', 'network_allocation', 'storage_budget', 'containment'):
            require(type(getattr(p, key)) is str and 0 < len(getattr(p, key)) <= 512,
                    'production-review-prerequisite-missing')
        require(all(type(d[k]) is int for k in ('not_before', 'expires')) and
                0 <= d['not_before'] <= time.time() < d['expires'] and
                0 < d['expires'] - d['not_before'] <= 3600,
                'production-review-expired')
        require(type(d['administrator']) is str and re.fullmatch(r'[A-Za-z0-9@._-]{1,128}', d['administrator']) and
                type(d['decision_id']) is str and re.fullmatch(r'[0-9a-f]{32}', d['decision_id']) and
                type(d['runtime_acceptance']) is str and 0 < len(d['runtime_acceptance']) <= 512,
                'production-review-origin-or-acceptance-missing')
        for key in ('caddy_runtime_hash', 'caddy_evidence_hash'):
            require(type(d[key]) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', d[key]),
                    'production-review-artifact-invalid')
        r = d['rollout']
        if d['phase'] == 'empty-host':
            require(r is None, 'production-review-premature-runtime-ids')
        else:
            require(type(r) is dict and set(r) == {'container_id', 'proxy_id', 'private_id', 'infrastructure'} and
                    all(type(r[k]) is str and re.fullmatch(r'[0-9a-f]{64}', r[k])
                        for k in ('container_id', 'proxy_id', 'private_id')) and
                    len({r[k] for k in ('container_id', 'proxy_id', 'private_id')}) == 3,
                    'production-review-runtime-ids-invalid')
            inventory = r['infrastructure']
            require(type(inventory) is list and 3 <= len(inventory) <= 16 and
                    all(type(row) is list and len(row) == 4 and
                        all(type(v) is str for v in row) and row[0] in ('network', 'container') and
                        re.fullmatch(r'[0-9a-f]{64}', row[1]) and 0 < len(row[2]) <= 128 and
                        re.fullmatch(r'sha256:[0-9a-f]{64}', row[3]) for row in inventory) and
                    len({tuple(row) for row in inventory}) == len(inventory),
                    'production-review-infrastructure-invalid')
            require({row[1] for row in inventory if row[0] == 'container'} == {r['container_id']} and
                    {r['proxy_id'], r['private_id']} <= {row[1] for row in inventory if row[0] == 'network'},
                    'production-review-infrastructure-unbound')

    def current(self):
        self.installation.verify()
        require(read_review() == self.document, 'production-review-revoked-or-replaced')
        self._validate()
        return self

    def verify(self, *args):
        self.current()
        if len(args) == 2:
            policy, identity = args
            require(type(policy) is HostPolicy and policy == self.policy and
                    identity == (policy.host, policy.daemon), 'production-review-policy-mismatch')
            return policy
        require(len(args) == 3, 'production-review-port-invalid')
        runtime, evidence, identity = args
        require(identity == (self.policy.host, self.policy.daemon) and type(evidence) is ImageEvidence and
                evidence.verified is True and evidence.reference == runtime.image and
                hashed(runtime.render().model) == self.document['caddy_runtime_hash'] and
                hashed(asdict(evidence)) == self.document['caddy_evidence_hash'],
                'production-review-runtime-acceptance-mismatch')
        return evidence

    def rollout_pins(self, journal, lease):
        """Data transition only, using the real lease-aware journal, never IDs invented by code.

        Existing composition/SourceTrust/PinnedCaddyRuntime still reobserve these
        approved identities before any candidate or mutation. No code update.
        """
        self.current()
        require(self.document['phase'] == 'rollout', 'production-review-rollout-not-approved')
        from scripts.production_integration.local_sources import ReadOnlyJournalSource
        require(type(journal) is ReadOnlyJournalSource, 'production-review-journal-source-invalid')
        state = journal.read(lease)
        a = state.get('bootstrapAuthorization')
        require(not state.get('incomplete') and not state.get('authorizations') and state.get('bootstrap') and
                a and a['state'] == 'succeeded', 'production-review-bootstrap-not-successful')
        c = parse_snapshot(a['payload'].encode())
        require(c['scope'] == 'first-bootstrap' and
                (c['host'], c['daemon']) == (self.policy.host, self.policy.daemon) and
                c['manager_sha'] == self.installation.manager_sha, 'production-review-bootstrap-unbound')
        return dict(self.document['rollout'])
