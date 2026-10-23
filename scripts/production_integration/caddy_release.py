"""Separate read-only Caddy provenance composition over existing trusted ports.

No ProjectRouter candidate, cache preparation, authorization or deployment scope.
Only installed code may supply the GitHub/registry/crypto ports and check policy.
"""
import re

from scripts.caddy_producer import IMAGE, REPOSITORY, WORKFLOW, PREDICATE, SLSA, BUILDER, published_inputs, load_lock
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import verify_oci_bytes
from scripts.production_integration.caddy_image import inspect_caddy_contract
from scripts.production_integration.release_source import ProtectedSource, PRODUCTION_REVIEW_POLICY
from scripts.production_integration.release_transport import require


class CaddyReleaseCollector:
    def __init__(self, *, github, registry, crypto, checks, publication_check):
        self.github, self.registry, self.crypto = github, registry, crypto
        self.checks, self.publication = tuple(checks), publication_check
        publication_check.validate()
        require(self.checks and all(c.repository == REPOSITORY for c in self.checks) and
                publication_check.repository == REPOSITORY and
                publication_check.workflow == '.github/workflows/caddy-image.yml' and
                publication_check.name == 'Publish trusted Caddy / Publish', 'caddy-release-policy-invalid')
        for check in self.checks:
            check.validate()

    def collect(self, runtime, source_sha):
        try:
            return self._collect(runtime, source_sha)
        except DockerError:
            raise
        except Exception:
            raise DockerError('caddy-release-evidence-incomplete') from None

    def _collect(self, runtime, source_sha):
        require(isinstance(source_sha, str) and re.fullmatch(r'[0-9a-f]{40}', source_sha) is not None and
                re.fullmatch(re.escape(IMAGE) + r'@sha256:[0-9a-f]{64}', runtime.image) is not None,
                'caddy-release-subject-invalid')
        lock = load_lock()
        require(runtime.version == lock['version'], 'caddy-release-version-mismatch')
        source = ProtectedSource(self.github, review_policy=PRODUCTION_REVIEW_POLICY)
        publication = source.collect(REPOSITORY, source_sha, self.checks, self.publication, source_sha, WORKFLOW)
        blobs = self.registry.collect(runtime.image)
        evidence = verify_oci_bytes(runtime.image, IMAGE, blobs)
        inspect_caddy_contract(runtime, evidence)
        attestations = self.github.get(REPOSITORY, '/attestations/' + runtime.image.split('@')[1] +
                                       '?per_page=100')['attestations']
        require(isinstance(attestations, list) and 0 < len(attestations) <= 10,
                'caddy-release-attestation-unavailable')
        identity = {'signerWorkflow': WORKFLOW, 'signerSha': source_sha,
                    'sourceSha': source_sha, 'sourceRef': 'refs/heads/main'}
        expected = published_inputs(lock, source_sha, *publication)
        statements = self.crypto.verify(blobs[runtime.image.split('@')[1]], runtime.image, identity,
            ({'predicateType': SLSA}, {'predicateType': PREDICATE, 'expectedPredicate': expected}),
            [a['bundle'] for a in attestations])
        require(isinstance(statements, tuple) and len(statements) == 2, 'caddy-release-signature-unverified')
        for statement, kind in zip(statements, (SLSA, PREDICATE)):
            require(statement.get('_type') == 'https://in-toto.io/Statement/v1' and
                    statement.get('predicateType') == kind and statement.get('subject') ==
                    [{'name': IMAGE, 'digest': {'sha256': runtime.image.split('@sha256:')[1]}}],
                    'caddy-release-signed-subject-mismatch')
        require(statements[0]['predicate']['runDetails']['builder']['id'] == BUILDER,
                'caddy-release-builder-mismatch')
        require(statements[1]['predicate'] == expected, 'caddy-release-inputs-mismatch')
        return evidence  # Source-verified only through this trusted installed composition.
