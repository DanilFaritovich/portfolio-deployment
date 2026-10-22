"""Strict release evidence verifier over an authenticated, separately installed port.

No GitHub token, HTTP client or attestation trust root is configured here. The
default blocks. release_collector.AuthenticatedReleaseCollector implements the
read-only installed port; CLI/CI never install it from user data. OCI byte/index/
config verification remains in artifacts.py.
"""
from dataclasses import asdict, dataclass

from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import SourceVerification, blob_digest
from scripts.reconciliation import encode

PRODUCER = 'DanilFaritovich/project-router'
MANAGER = 'DanilFaritovich/portfolio-deployment'
ORIGIN = 'https://danil-miftakhov.dev'


@dataclass(frozen=True)
class ReleaseFacts:
    # Only authenticated collector output, never a deserialized user document.
    protected_main: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, str, str, int], ...]  # repo, SHA, required check, issuing app ID
    publication: tuple[str, str, str, str]  # repo, SHA, refs/heads/main, exact image reference
    provenance: tuple[str, str, str, str]  # subject ref, source repo, source SHA, builder identity
    build_origin: str
    manifest_hash: str
    policy_hash: str
    candidate_hash: str


class ReleaseVerifier:
    def __init__(self, candidate, *, required_checks, allowed_builders, collector=None):
        candidate.validate()
        self.candidate = candidate
        self.required_checks = frozenset(required_checks)  # (repo, name, app ID), operator policy
        self.builders = frozenset(allowed_builders)  # exact workflow identity incl. immutable workflow revision
        self.collector = collector

    def verify(self, request):
        c = self.candidate
        if (request.repository != PRODUCER or request.manager_repository != MANAGER or
            request.producer_revision != c.producer_sha or request.manager_revision != c.manager_sha or
            request.reference != c.image or request.build_origin != ORIGIN):
            raise DockerError('production-release-candidate-mismatch')
        if self.collector is None:
            raise DockerError('production-github-ghcr-attestation-verifier-unconfigured')
        if (not self.required_checks or not self.builders or
            {repo for repo, _, _ in self.required_checks} != {PRODUCER, MANAGER}):
            raise DockerError('production-release-verification-policy-incomplete')
        # Collector independently authenticates GitHub rules/ancestry and
        # successful same-SHA checks/publication, GHCR subject and signed SLSA
        # provenance incl. builder and origin build argument. Labels/tags and
        # unsigned JSON cannot implement this contract. Missing API/attestation
        # capabilities MUST raise, never return cached unverified claims.
        facts = self.collector.collect_authenticated(request, self.required_checks, self.builders)
        expected_main = {(PRODUCER, c.producer_sha), (MANAGER, c.manager_sha)}
        expected_checks = {(repo, c.producer_sha if repo == PRODUCER else c.manager_sha, name, app)
                           for repo, name, app in self.required_checks}
        if (not isinstance(facts, ReleaseFacts) or set(facts.protected_main) != expected_main or
            set(facts.checks) != expected_checks or
            facts.publication != (PRODUCER, c.producer_sha, 'refs/heads/main', c.image) or
            facts.provenance[:3] != (c.image, PRODUCER, c.producer_sha) or len(facts.provenance) != 4 or
            facts.provenance[3] not in self.builders or facts.build_origin != ORIGIN or
            (facts.manifest_hash, facts.policy_hash, facts.candidate_hash) !=
            (c.manifest_hash, c.policy_hash, c.identity)):
            raise DockerError('production-release-evidence-incomplete-or-mismatched')
        return SourceVerification(request, (), blob_digest(encode(asdict(facts)).encode()))
