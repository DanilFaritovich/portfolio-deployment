"""Explicit read-only readiness API for reviewed installation code, never PR CI.

This module accepts typed installation policy, not JSON, manifests, module paths,
environment defaults or saved ReleaseFacts. Installed Python remains a trusted
code boundary (ADR 0001). It issues neither evidence exports nor capabilities.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import SourceRequest
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.release import MANAGER, ORIGIN, PRODUCER, ReleaseVerifier
from scripts.production_integration.release_collector import AuthenticatedReleaseCollector
from scripts.production_integration.release_crypto import AttestationVerifier
from scripts.production_integration.release_source import WorkflowCheck
from scripts.production_integration.release_transport import GHCRRead, GitHubRead, HTTPSRead, LIMIT, require


@dataclass(frozen=True)
class ReleaseInstallation:
    candidate: OperationCandidate
    checks: tuple[WorkflowCheck, ...]
    publication_check: WorkflowCheck
    signer_sha: str
    builder: str
    slsa_builder: str
    gh_binary: Path
    gh_sha256: str
    trusted_root: Path
    root_sha256: str
    manifest_path: str = 'registry/applications/project-router.yaml'

    def validate(self):
        require(type(self.candidate) is OperationCandidate and type(self.checks) is tuple and
                all(type(c) is WorkflowCheck for c in self.checks) and
                type(self.publication_check) is WorkflowCheck,
                'production-release-installation-invalid')
        self.candidate.validate()
        # Check pins before the first network read; the crypto adapter repeats
        # this on private copies immediately before execution to prevent swaps.
        for path, digest, bound in ((self.gh_binary, self.gh_sha256, 128 * LIMIT),
                                    (self.trusted_root, self.root_sha256, LIMIT)):
            require(isinstance(path, Path) and path.is_absolute() and not path.is_symlink() and
                    isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) is not None,
                    'production-release-crypto-installation-invalid')
            with path.open('rb') as stream:
                raw = stream.read(bound + 1)
            require(0 < len(raw) <= bound and hashlib.sha256(raw).hexdigest() == digest,
                    'production-release-crypto-installation-invalid')


# Only these public diagnostics escape. All other exceptions become one fixed
# blocker, even if a trusted dependency accidentally includes remote text.
PUBLIC_BLOCKERS = frozenset({
    'production-release-sha-not-on-main',
    'production-release-protection-insufficient',
    'production-release-protection-unverifiable',
    'production-release-protection-app-unbound',
    'production-release-required-check-policy-mismatch',
    'production-release-required-check-unavailable',
    'production-release-required-check-untrusted',
    'production-release-check-workflow-mismatch',
    'production-release-publication-unverified',
    'production-release-source-changed',
    'production-release-api-denied-or-rate-limited',
    'production-release-api-not-found',
    'production-release-api-pagination-unproven',
    'production-release-api-timeout-or-unavailable',
    'production-release-budget-exhausted',
    'production-release-crypto-installation-invalid',
    'production-release-crypto-version-invalid',
    'production-release-signature-unverified',
    'production-release-signed-claims-mismatch',
    'production-release-builder-mismatch',
    'production-release-manager-content-mismatch',
})


def check_release_readiness(installation, *, authorize_read_only=False, github_token=None):
    """Call only after separate permission using independently reviewed pins.

    Token must have read-only repository permissions; this API cannot discover
    or certify its grants. The opt-in records caller intent, not production
    authorization. Return a short anonymous report and discard verification facts.
    A fresh bounded transport is created for each invocation; no cache fallback.
    """
    blocker = None
    if authorize_read_only is not True:
        blocker = 'production-release-read-only-opt-in-required'
    elif type(installation) is not ReleaseInstallation:
        blocker = 'production-release-reviewed-installation-required'
    else:
        try:
            installation.validate()
            i = installation
            transport = HTTPSRead()
            collector = AuthenticatedReleaseCollector(i.candidate,
                github=GitHubRead(github_token, transport), registry=GHCRRead(transport),
                crypto=AttestationVerifier(i.gh_binary, i.gh_sha256, i.trusted_root, i.root_sha256),
                checks=i.checks, publication_check=i.publication_check, signer_sha=i.signer_sha,
                builder=i.builder, slsa_builder=i.slsa_builder, manifest_path=i.manifest_path)
            verifier = ReleaseVerifier(i.candidate, collector=collector,
                required_checks=[(p.repository, p.name, p.app_id) for p in i.checks], allowed_builders=[i.builder])
            c = i.candidate
            result = verifier.verify(SourceRequest(PRODUCER, c.producer_sha, c.manager_sha, c.image, ORIGIN, MANAGER))
            require(not result.blockers, 'production-release-evidence-incomplete')
        except Exception as error:
            code = str(error) if isinstance(error, DockerError) else ''
            blocker = code if code in PUBLIC_BLOCKERS else 'production-release-readiness-unverified'
    return {'readOnly': True, 'releaseVerified': blocker is None,
            'productionAuthorized': False, 'blockers': [] if blocker is None else [blocker]}
