"""Measured host facts -> independently installed policy -> HostContracts.

Exact policy deliberately blocks unknown routing, listeners, IPAM and contents.
It is not loaded from inspection JSON and cannot approve backup/console drills.
Those require a separate installed evidence authority on every collection.
"""
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import stat
import time

from scripts.docker_adapter import DockerError
from scripts.production_integration.network_inventory import network_subnets
from scripts.docker_discovery import discover
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.candidate import HostContracts, hashed
from scripts.production_integration.installation import protected
from scripts.production_integration.admin_contract import HOST_DIRECTORY, HOST_SOCKET
from scripts.production_integration.admin_socket import socket_metadata
from scripts.production_integration.local_sources import PATHS
from scripts.production_integration.observations import require
from scripts.reconciliation import encode


@dataclass(frozen=True)
class HostPolicy:
    host: str
    daemon: str
    network_hash: str  # complete kernel routes/rules/listeners + Engine network facts
    resources_hash: str  # full separately reviewed Engine universe (per operation phase)
    storage_hash: str  # actual metadata/content ownership, excluding capacity counters
    minimum_bytes: int
    minimum_inodes: int
    backup_restore: str  # independently authenticated evidence references, not booleans
    console_recovery: str
    network_allocation: str
    storage_budget: str
    containment: str

    @property
    def identity(self):
        return hashed(asdict(self))


def storage_contents():
    """Bounded fixed-path read, never initializes/adopts data or reads credentials.

    Certificates are hashed only; no content escapes. Journal is independently
    validated under the lease. Request bytes are excluded to avoid a self-referent
    candidate; metadata still must satisfy protected().
    """
    entries, total = [], 0
    for directory in PATHS:
        root = Path(directory)
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            is_dir = stat.S_ISDIR(info.st_mode)
            is_socket = str(path) == HOST_SOCKET and stat.S_ISSOCK(info.st_mode)
            if is_socket:
                socket_metadata(info)
            else:
                protected(path, directory=is_dir)
            require(not info.st_mode & 0o077, 'production-storage-content-mode-invalid')
            record = {'path': str(path), 'uid': info.st_uid, 'gid': info.st_gid,
                      'mode': stat.S_IMODE(info.st_mode), 'device': info.st_dev, 'inode': info.st_ino}
            if is_dir:
                if directory != '/srv/portfolio/journal':
                    with os.scandir(path) as children:
                        for child in children:
                            pending.append(Path(child.path))
                            require(len(entries) + len(pending) <= 4096, 'production-storage-content-limit')
            elif not is_socket and directory not in ('/srv/portfolio/requests', '/srv/portfolio/locks'):
                total += info.st_size
                require(total <= 32 * 1024 * 1024, 'production-storage-content-limit')
                import hashlib
                with path.open('rb') as stream:
                    opened = os.fstat(stream.fileno())
                    require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), 'production-storage-content-changed')
                    record['digest'] = hashlib.sha256(stream.read(info.st_size + 1)).hexdigest()
                    require(os.fstat(stream.fileno()) == opened and path.lstat() == info,
                            'production-storage-content-changed')
            require(directory != HOST_DIRECTORY or is_dir and path == root or is_socket,
                    'production-admin-directory-foreign-content')
            # The request is published AFTER signing the candidate. Validate its
            # protection, but do not bind its existence/inode back into itself.
            if directory != '/srv/portfolio/requests' or is_dir:
                entries.append(record)
            require(len(entries) + len(pending) <= 4096, 'production-storage-content-limit')
    return tuple(sorted(entries, key=lambda r: r['path']))


def network_facts(host, docker):
    return {'kernel': host.network, 'networks': [asdict(r) for r in docker.resources if r.kind == 'network'],
            'hostNetworking': [asdict(r) for r in docker.resources if r.kind == 'container' and
                               r.facts['host'].get('NetworkMode') == 'host']}


def validate_network_inventory(docker):
    for resource in docker.resources:
        if resource.kind == 'network':
            network_subnets(resource)


class PolicyHostContracts:
    def __init__(self, installation, engine, local, journal, policy, *, prerequisites=None,
                 contents=storage_contents, clock=time.monotonic, facts_policy=None):
        self.installation, self.engine, self.local, self.journal = installation, engine, local, journal
        self.policy, self.prerequisites, self.contents, self.clock = policy, prerequisites, contents, clock
        self.facts_policy = facts_policy
        self.last = None

    def collect(self):
        raise DockerError('production-host-contract-canonical-lease-required')

    def collect_under_lease(self, lease, *, before_observation=None):
        self.last = None
        self.installation.verify()
        start = None
        p = self.policy
        require(type(p) is HostPolicy and self.prerequisites is not None and
                all(type(v) is str and v for v in (p.backup_restore, p.console_recovery,
                    p.network_allocation, p.storage_budget, p.containment)) and
                type(p.minimum_bytes) is int and p.minimum_bytes >= 1024**3 and
                type(p.minimum_inodes) is int and p.minimum_inodes > 0,
                'production-host-independent-policy-incomplete')
        identity = self.engine.identity()
        require(identity == (p.host, p.daemon), 'production-host-policy-identity-mismatch')
        # Authority verifies policy/evidence origin, validity, revocation and exact
        # host binding; no local file or reference string can implement this alone.
        def begin_observation():
            nonlocal start
            require(start is None, 'production-host-observation-window-unbound')
            require(self.prerequisites.verify(p, identity) == p, 'production-host-prerequisites-unverified')
            if before_observation is not None:
                before_observation()
            start = self.clock()
        host = self.local.collect(before_observation=begin_observation)
        require(start is not None, 'production-host-observation-window-unbound')
        docker = discover(self.engine, ())
        require(host.identity == identity and docker.complete, 'production-host-inventory-incomplete')
        validate_network_inventory(docker)
        network, contents = network_facts(host, docker), self.contents()
        if self.facts_policy is None:
            require(hashed(network) == p.network_hash and hashed(asdict(docker)) == p.resources_hash,
                    'production-host-network-or-resource-policy-mismatch')
            require(hashed(contents) == p.storage_hash, 'production-host-storage-policy-mismatch')
        else:
            self.facts_policy.validate_facts(p, host, docker, network, contents)
        require({r['path'] for r in host.storage} == set(PATHS) and
                all(r['availableBytes'] >= p.minimum_bytes and r['availableInodes'] >= p.minimum_inodes
                    and r['uid'] == r['gid'] == 0 and r['mode'] == 0o700 for r in host.storage),
                'production-host-storage-policy-mismatch')
        journal = self.journal.read(lease)
        require(not journal.get('incomplete') and not journal.get('authorizations'), 'production-host-recovery-required')
        require(journal.get('database') in ('absent', 'present'), 'production-host-journal-unknown')
        baseline = journal.get('bootstrap')
        authorization = journal.get('bootstrapAuthorization')
        verified = False
        if baseline is not None:
            require(authorization is not None and authorization['state'] == 'succeeded', 'production-host-bootstrap-ambiguous')
            candidate = parse_snapshot(authorization['payload'].encode())
            require(candidate['scope'] == 'first-bootstrap' and (candidate['host'], candidate['daemon']) == identity,
                    'production-host-bootstrap-identity-mismatch')
            verified = True
        else:
            require(authorization is None, 'production-host-bootstrap-ambiguous')
        require(discover(self.engine, ()) == docker and self.contents() == contents and
                identity == self.engine.identity() and start <= host.captured_at <= self.clock() and
                self.clock() - start <= 5, 'production-host-facts-changed-or-expired')
        # Stable policy/identities and measured state bind candidate identity;
        # volatile capacity/capture counters are threshold checks, not state hashes.
        result = HostContracts(*identity, self.clock(), encode({'policy': p.identity, 'facts': network}),
            encode({'policy': p.identity, 'contents': contents}),
            encode({'policy': p.identity, 'journal': journal}), verified)
        self.last = (result, hashed(asdict(result)), identity)
        return result

    def verify(self, contracts, identity):
        require(self.last is not None and contracts is self.last[0] and
                hashed(asdict(contracts)) == self.last[1] and identity == self.last[2] == self.engine.identity(),
                'production-host-policy-proof-unbound')
        return contracts
