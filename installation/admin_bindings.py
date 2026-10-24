"""Independently authenticated administrative channel: NOT an executor approval.

Install this reviewed binding alongside the independently pinned standalone helper,
never from the install bundle. The fixed intake consumes separately published
administrator authority; no installation decision is supplied by this repo.
"""

from dataclasses import dataclass
import hashlib
import re
import time


def authenticate_initial_installation(bundle_sha256):
    try:
        return authenticated_file_decision(bundle_sha256)
    except ModuleNotFoundError as error:
        if error.name != 'operator_bootstrap':
            raise
        raise RuntimeError('independent-administrative-helper-unavailable') from None


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


def authenticated_file_decision(bundle_sha256):
    """Opt-in fixed administrative intake; selected by the fixed administrative binding.

    The administrator separately installs a reviewed 0600 decision file through
    the authenticated portfolio_admin channel, OUTSIDE the bundle. Root file
    publication is the approval act. There is no document generator or issuer.
    Lost/failed consumption requires a new administrative review, never replay.
    """
    from dataclasses import fields
    import os
    from pathlib import Path
    from operator_bootstrap import (INPUT, admin_context, canonical, digest,
                                    exclusive, json_bytes, metadata, require, sync_directory)
    admin_context()
    directory = INPUT.parent
    path = directory / 'installation-decision.json'
    metadata(directory, directory=True, private=True)
    metadata(path, private=True)
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        raw = stream.read(2 * 1024 * 1024 + 1)
        require(0 < len(raw) <= 2 * 1024 * 1024 and
                os.fstat(stream.fileno()) == info and path.lstat() == info,
                'administrative-decision-changed')
    document = json_bytes(raw)
    require(type(document) is dict and set(document) == {f.name for f in fields(InitialInstallationDecision)} and
            canonical(document) == raw, 'administrative-decision-fields-invalid')
    require(type(document['external_files']) is list and
            all(type(p) is list and len(p) == 2 for p in document['external_files']),
            'administrative-decision-inventory-invalid')
    document['external_files'] = tuple(tuple(p) for p in document['external_files'])
    decision = InitialInstallationDecision(**document)

    class ChannelAuthority:
        def consume(self, presented):
            require(presented is decision and Path('/etc/machine-id').read_text().strip() == decision.machine_id,
                    'administrative-decision-host-mismatch')
            metadata(INPUT, private=True)
            with INPUT.open('rb') as stream:
                require(digest(stream.read(128 * 1024 * 1024 + 1)) == decision.bundle_sha256,
                        'administrative-decision-intake-mismatch')
            for filename, pin in decision.external_files:
                target = Path(filename)
                metadata(target)
                with target.open('rb') as stream:
                    require(hashlib.file_digest(stream, 'sha256').hexdigest() == pin,
                            'administrative-decision-external-content-changed')
            require(path.lstat() == info and decision.not_before <= time.time() < decision.expires,
                    'administrative-decision-revoked-or-expired')
            # Retained exclusive marker is intent BEFORE installer mutation.
            # Concurrent invocations cannot both consume; fsync failure fences.
            exclusive(directory / ('spent-' + decision.decision_id), raw)
            sync_directory(directory)
            return decision

    return authenticate_decision(bundle_sha256, decision, authority=ChannelAuthority())
