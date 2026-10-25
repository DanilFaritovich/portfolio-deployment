"""Journal gates for Manager updates; absence requires independent first-use authority."""
from installation.operator_bootstrap import exclusive
from scripts.cd.upgrade import verify_tree
from scripts.production_integration.administrative_review import AdministrativePhaseReview
from scripts.production_integration.docker import EngineContract
from scripts.production_integration.first_phase import EmptyHostReader
from scripts.production_integration.installation import protected
from scripts.production_integration.local_sources import (ReadOnlyJournalSource, installed_engine,
                                                        LocalHostSources)
from scripts.production_integration.locking import require_production_lease
from scripts.production_integration.observations import require


def initial_review(installed, policy):
    """Fresh admin authority AND complete empty-host observations, never audit JSON."""
    review = AdministrativePhaseReview(installed)
    require(review.document['phase'] == 'empty-host' and
            (review.policy.host, review.policy.daemon) == (policy.document['host'], policy.document['daemon']),
            'cd-initial-journal-review-required')
    contract = EngineContract('unix:///run/docker.sock', review.policy.daemon, review.policy.host, True)
    engine = installed_engine(installed, contract)
    EmptyHostReader(installed, engine, LocalHostSources(installed, engine), review.policy, review).collect()
    review.current()


def check(manager, lease):
    require_production_lease(lease)
    manager.policy.current()
    manager.installation.verify()
    source = ReadOnlyJournalSource()
    state = source.read(lease)
    established = manager.state / 'journal-established.json'
    if state.get('database') == 'present':
        # Remember even an incomplete journal before rejecting it. Its later loss
        # must never reopen the first-use exception. This is outside SQLite.
        if not established.exists() and not established.is_symlink():
            exclusive(established, b'{"database":"established"}')
        protected(established)
        require('incomplete' in state and 'authorizations' in state and
                not state['incomplete'] and not state['authorizations'], 'cd-journal-recovery-required')
        return
    require(state == {'database': 'absent', 'recovery': 'unestablished'}, 'cd-journal-recovery-required')
    require(not established.exists() and not established.is_symlink(), 'cd-journal-history-lost')
    require(manager.policy.document.get('initial_journal') == 'reviewed-empty-host',
            'cd-initial-journal-not-authorized')
    initial = manager.policy.document['initial_manager_sha']
    records = manager.recovery_report()
    require(all(r['from'] == initial and r['status'] not in ('succeeded', 'no-op') for r in records),
            'cd-journal-history-lost')
    # A pending first exchange may need separately signed recovery on the new
    # tree. Only that exact recorded tree is eligible, with a NEW admin review
    # bound to the currently installed SHA. Ordinary delivery remains fenced.
    if manager.installation.manager_sha != initial:
        active = verify_tree(manager.root)
        require(any(r['status'] in ('switch-intent', 'verifying') and r.get('new_inventory') == active
                    for r in records), 'cd-initial-manager-required')
    initial_review(manager.installation, manager.policy)
    manager.policy.current()
    require(source.read(lease) == state, 'cd-journal-changed')
    require_production_lease(lease)
