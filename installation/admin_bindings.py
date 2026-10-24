"""Independently authenticated administrative channel: NOT an executor approval.

Install this reviewed binding alongside the independently pinned standalone helper,
never from the install bundle. No authentication policy is selected in this repo.
"""

from dataclasses import dataclass
import hashlib
import re
import time


def authenticate_initial_installation(bundle_sha256):
    raise RuntimeError('independent-administrative-installation-channel-unconfigured')


# This typed contract is consumed only by independently installed administrative
# code. There is deliberately no JSON/env loader or built-in approval issuer.
@dataclass(frozen=True)
class InitialInstallationDecision:
    machine_id: str
    manager_sha: str
    bundle_sha256: str
    helper_sha256: str
    binding_sha256: str
    external_inventory_sha256: str
    external_files: tuple[tuple[str, str], ...]
    decision_id: str
    not_before: int
    expires: int


def authenticate_decision(bundle_sha256, decision, *, authority, now=None):
    """Validate exact pins before the external authority consumes its one-use grant.

    authority.consume(decision) must authenticate the administrator, protected-main
    source/bundle review, current machine, intake and external files, freshness and
    revocation; it atomically prevents reuse and returns the SAME decision object.
    Neither a matching digest nor this dataclass establishes that authority.
    The installer independently rechecks actual machine/files/bundle before writes.
    """
    from operator_bootstrap import InitialInstallationApproval, canonical
    def require(value):
        if not value:
            raise RuntimeError('independent-administrative-installation-decision-invalid')
    now = time.time() if now is None else now
    require(type(decision) is InitialInstallationDecision and authority is not None)
    d = decision
    require(type(d.machine_id) is str and re.fullmatch(r'[0-9a-f]{32}', d.machine_id) and
            type(d.manager_sha) is str and re.fullmatch(r'[0-9a-f]{40}', d.manager_sha))
    require(all(type(pin) is str and re.fullmatch(r'[0-9a-f]{64}', pin) for pin in (
        d.bundle_sha256, d.helper_sha256, d.binding_sha256, d.external_inventory_sha256)))
    require(bundle_sha256 == d.bundle_sha256 and type(d.external_files) is tuple and
            0 < len(d.external_files) <= 8192)
    require(all(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is str and
                pair[0].startswith('/') and type(pair[1]) is str and re.fullmatch(r'[0-9a-f]{64}', pair[1])
                for pair in d.external_files))
    files = dict(d.external_files)
    require(len(files) == len(d.external_files) and tuple(sorted(d.external_files)) == d.external_files)
    root = '/usr/local/lib/portfolio-deployment-bootstrap/'
    require(files.get(root + 'operator_bootstrap.py') == d.helper_sha256 and
            files.get(root + 'admin_bindings.py') == d.binding_sha256 and
            hashlib.sha256(canonical(list(map(list, d.external_files)))).hexdigest() == d.external_inventory_sha256)
    require(type(d.decision_id) is str and re.fullmatch(r'[0-9a-f]{32}', d.decision_id) and
            type(d.not_before) is int and type(d.expires) is int and
            0 <= d.not_before <= now < d.expires and 0 < d.expires - d.not_before <= 3600)
    require(authority.consume(d) is d)
    return InitialInstallationApproval(d.bundle_sha256, d.manager_sha, d.machine_id, d.external_files)
