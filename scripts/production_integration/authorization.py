"""Candidate binding and independent operator signature verification contract."""
from dataclasses import asdict, dataclass
import math
import re

from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import blob_digest
from scripts.reconciliation import encode


@dataclass(frozen=True)
class OperationCandidate:
    host: str
    daemon: str
    manager_sha: str
    producer_sha: str
    image: str
    manifest_hash: str
    policy_hash: str
    plan_hash: str
    gateway_hash: str
    runtime_hash: str
    network_hash: str
    storage_hash: str
    recovery_hash: str
    scope: str

    def validate(self):
        if (not all(isinstance(value, str) for value in asdict(self).values()) or
            not re.fullmatch(r'[0-9a-f]{32}', self.host) or
            not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', self.daemon) or
            not all(re.fullmatch(r'[0-9a-f]{40}', s) for s in (self.manager_sha, self.producer_sha)) or
            not re.fullmatch(r'ghcr.io/danilfaritovich/project-router@sha256:[0-9a-f]{64}', self.image) or
            self.scope not in ('first-bootstrap', 'first-project-router-rollout', 'prepare-project-router-image',
                               'update-project-router', 'manager-upgrade')):
            raise DockerError('production-candidate-identity-invalid')
        for key, value in asdict(self).items():
            if key.endswith('_hash') and not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
                raise DockerError('production-candidate-contract-hash-invalid')

    @property
    def identity(self):
        self.validate()
        return blob_digest(encode(asdict(self)).encode())


@dataclass(frozen=True)
class ApprovalClaims:
    candidate: str
    operator: str
    not_before: float
    expires: float
    nonce: str


def review_approval(candidate, envelope, *, verifier=None, now):
    """Verifier must authenticate operator signature, issuer allowlist and nonce.

    This returns validated claims for review, never an executor capability. A
    fixture, merge, CI or plain dict cannot become an approval by parsing it.
    The one-shot coordinator consumes these claims in the durable journal ledger
    before creating an in-process execution grant. This function alone grants none.
    """
    identity = candidate.identity
    if verifier is None:
        raise DockerError('production-operator-approval-verifier-unavailable')
    claims = verifier.authenticate(envelope)
    if (not isinstance(claims, ApprovalClaims) or claims.candidate != identity or
        not isinstance(claims.operator, str) or not re.fullmatch(r'[A-Za-z0-9@._-]{1,128}', claims.operator) or
        not isinstance(claims.nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', claims.nonce) or
        not all(type(t) in (int, float) and math.isfinite(t) for t in (now, claims.not_before, claims.expires)) or
        not claims.not_before <= now < claims.expires or not 0 < claims.expires - claims.not_before <= 3600):
        raise DockerError('production-approval-stale-or-binding-invalid')
    return claims
