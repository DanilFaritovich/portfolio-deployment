"""Standing host invariants plus fresh existing Engine/storage/journal evidence."""
from dataclasses import asdict
from pathlib import Path

from scripts.cd.policy import pin
from scripts.compose import LABEL
from scripts.production_integration.candidate import hashed
from scripts.production_integration.host_policy import HostPolicy
from scripts.production_integration.local_sources import ReadOnlyJournalSource, PATHS
from scripts.production_integration.observations import require
from scripts.production_integration.runtime_sources import RuntimeInventory
from scripts.planning_inputs import parse_snapshot


class StandingReview:
    """Signed invariants outlive a release; all execution evidence stays fresh.

    Initial bootstrap/first rollout still use AdministrativePhaseReview. This
    authority permits neither creation nor renewal of an hourly phase review.
    """
    def __init__(self, installation, policy):
        self.installation, self.standing = installation, policy
        d = policy.current().document['host_contract']
        require(set(d) == {'host_policy', 'kernel_sha256', 'storage_roots', 'caddyfile_sha256',
                           'caddy_runtime_hash', 'caddy_evidence_hash', 'rollout'}, 'cd-host-contract-invalid')
        self.policy = HostPolicy(**d['host_policy'])
        require((self.policy.host, self.policy.daemon) == (policy.document['host'], policy.document['daemon']),
                'cd-host-identity-mismatch')
        require(set(d['storage_roots']) == set(PATHS), 'cd-storage-policy-incomplete')
        self.document = {'phase': 'rollout', **d}
        self.engine = self.resolve_image = None
        self.catalog = ()

    def current(self):
        self.installation.verify()
        self.standing.current()
        return self

    def verify(self, *args):
        self.current()
        if len(args) == 2:
            policy, identity = args
            require(policy == self.policy and identity == (policy.host, policy.daemon), 'cd-host-policy-unbound')
            return policy
        runtime, evidence, identity = args
        require(identity == (self.policy.host, self.policy.daemon) and evidence.verified is True and
                evidence.reference == runtime.image and hashed(runtime.render().model) == self.document['caddy_runtime_hash'] and
                hashed(asdict(evidence)) == self.document['caddy_evidence_hash'], 'cd-caddy-policy-unbound')
        return evidence

    def rollout_pins(self, journal, lease):
        self.current()
        require(type(journal) is ReadOnlyJournalSource, 'cd-journal-source-required')
        state = journal.read(lease)
        auth = state.get('bootstrapAuthorization')
        require(state.get('bootstrap') and auth and auth['state'] == 'succeeded' and
                not state.get('incomplete') and not state.get('authorizations'), 'cd-bootstrap-baseline-required')
        candidate = parse_snapshot(auth['payload'].encode())
        require(candidate['scope'] == 'first-bootstrap' and
                (candidate['host'], candidate['daemon']) == (self.policy.host, self.policy.daemon), 'cd-bootstrap-unbound')
        return dict(self.document['rollout'])

    def validate_facts(self, policy, host, docker, network, contents):
        self.current()
        d = self.document
        require(policy == self.policy and hashed(host.network) == d['kernel_sha256'], 'cd-kernel-policy-drift')
        roots = {r['path']: r for r in contents if r['path'] in d['storage_roots']}
        require(set(roots) == set(d['storage_roots']) and
                all({k: roots[p][k] for k in ('uid', 'gid', 'mode', 'device', 'inode')} == expected
                    for p, expected in d['storage_roots'].items()), 'cd-storage-root-changed')
        require(pin(Path('/srv/portfolio/caddy-config/Caddyfile').read_bytes()) == d['caddyfile_sha256'],
                'cd-gateway-config-drift')
        require(self.engine is not None and self.resolve_image is not None, 'cd-host-catalog-unconfigured')
        references = {r.facts['config'].get('Image') for r in docker.resources if r.kind == 'container' and
                      r.facts['labels'].get(LABEL + '.application') == 'project-router'}
        selected = tuple(a for a in self.catalog if {c['image'] for c in a.spec['components']} <= references)
        require(bool(selected), 'cd-application-baseline-required')
        inventory = RuntimeInventory(self.engine, selected, self.resolve_image, d['rollout']['infrastructure'])
        _, observed, _ = inventory.capture()
        require(observed.resources == docker.resources, 'cd-host-runtime-round-changed')
