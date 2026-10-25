"""Finite, sealed first-installation recipe. No request/env-selected dependencies.

Both recipes and all tools/authorities must be reviewed in bindings.py BEFORE
installation. The separately published admin review supplies measured data only.
This module never publishes a review, updates code or issues an operation approval.
"""
from scripts.production_integration.administrative_review import AdministrativePhaseReview
from scripts.production_integration.composition import build as build_rollout
from scripts.production_integration.first_phase import build_first_phase
from scripts.production_integration.local_sources import ReadOnlyJournalSource
from scripts.production_integration.locking import production_host_lock
from scripts.production_integration.observations import require
from scripts.production_integration.runtime import ProxyContract


def build_reviewed_phase(installation, *, empty_host, rollout, review=None):
    """Arguments are fixed sealed Python wiring, never deserialized data.

    empty_host contains all build_first_phase inputs except host_policy,
    prerequisites and caddy_acceptance; rollout contains all composition.build
    inputs except host_policy, prerequisites, proxy, private_network_id,
    infrastructure, active_pins, active_channel and gateway_runtime.
    Unapproved inputs leave the shipped binding raising, even with a review file.
    """
    review = AdministrativePhaseReview(installation) if review is None else review
    policy = review.policy
    if review.document['phase'] == 'empty-host':
        return build_first_phase(installation, host_policy=policy, prerequisites=review,
                                 caddy_acceptance=review, **empty_host)
    with production_host_lock() as lease:
        pins = review.rollout_pins(ReadOnlyJournalSource(), lease)
        proxy = rollout['bootstrap_proxy']
        require((rollout['engine_contract'].host_id, rollout['engine_contract'].daemon_id) ==
                (policy.host, policy.daemon), 'production-review-engine-mismatch')
        review.verify(rollout['caddy'], rollout['caddy_image'], (policy.host, policy.daemon))
        return build_rollout(installation, host_policy=policy, prerequisites=review,
            proxy=ProxyContract(proxy.subnet, proxy.gateway, pins['proxy_id']),
            private_network_id=pins['private_id'], infrastructure=tuple(tuple(r) for r in pins['infrastructure']),
            active_pins=(pins['container_id'], pins['proxy_id']), active_channel=None, gateway_runtime=None,
            **rollout)
