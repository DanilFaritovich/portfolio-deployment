"""Authenticated evidence composition. No execution or production wiring.

All injected ports are trusted installation code (the existing trust boundary),
not constructors from API/files/manifest flags. Errors disclose fixed codes only.
"""
import base64
import re

from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import blob_digest, verify_oci_bytes
from scripts.production_integration.release import ReleaseFacts, PRODUCER, MANAGER, ORIGIN
from scripts.production_integration.release_source import ProtectedSource
from scripts.production_integration.release_transport import require, IMAGE_REPOSITORY, LIMIT

SLSA = 'https://slsa.dev/provenance/v1'
BUILD_ORIGIN = 'https://github.com/' + PRODUCER + '/attestations/build-origin/v1'


class AuthenticatedReleaseCollector:
    def __init__(self, candidate, *, github, registry, crypto, checks, signer_sha,
                 builder, slsa_builder, publication_check, manifest_path='registry/applications/project-router.yaml'):
        candidate.validate()
        self.candidate, self.github, self.registry, self.crypto = candidate, github, registry, crypto
        self.checks, self.signer_sha, self.builder = tuple(checks), signer_sha, builder
        self.workflow = '.github/workflows/publish.yml'
        publication_check.validate()
        require(publication_check.repository == PRODUCER and
                publication_check.workflow == '.github/workflows/ci.yml',
                'production-release-workflow-policy-invalid')
        self.publication_check = publication_check
        self.slsa_builder = slsa_builder
        require(isinstance(slsa_builder, str) and slsa_builder.startswith('https://github.com/') and
                len(slsa_builder) <= 256, 'production-release-builder-policy-invalid')
        self.manifest_path = manifest_path
        require(manifest_path in ('registry/applications/project-router.yaml', 'docs/candidates/project-router.yaml'),
                'production-release-manifest-path-rejected')
        require(re.fullmatch(r'[0-9a-f]{40}', signer_sha) is not None and
                builder == 'https://github.com/' + PRODUCER + '/' + self.workflow + '@' + signer_sha,
                'production-release-builder-policy-invalid')
        for check in self.checks:
            check.validate()
        require(len(self.checks) == len({(c.repository, c.name, c.app_id) for c in self.checks}) and
                {c.repository for c in self.checks} == {PRODUCER, MANAGER},
                'production-release-workflow-policy-invalid')

    def _content_hash(self, path):
        doc = self.github.get(MANAGER, '/contents/' + path + '?ref=' + self.candidate.manager_sha)
        require(doc['type'] == 'file' and doc['path'] == path and doc['encoding'] == 'base64' and
                type(doc['size']) is int and 0 < doc['size'] <= LIMIT)
        raw = base64.b64decode(doc['content'].replace('\n', ''), validate=True)
        require(len(raw) == doc['size'])
        return blob_digest(raw)

    def collect_authenticated(self, request, required_checks, builders):
        try:
            return self._collect(request, required_checks, builders)
        except DockerError:
            raise
        except Exception:
            # Never expose KeyError values, response text, certificate contents,
            # credential-bearing URLs or subprocess output through diagnostics.
            raise DockerError('production-release-evidence-incomplete') from None

    def _collect(self, request, required_checks, builders):
        c = self.candidate
        require((request.repository, request.producer_revision, request.manager_repository,
                 request.manager_revision, request.reference, request.build_origin) ==
                (PRODUCER, c.producer_sha, MANAGER, c.manager_sha, c.image, ORIGIN),
                'production-release-candidate-mismatch')
        require(set(required_checks) == {(p.repository, p.name, p.app_id) for p in self.checks} and
                self.builder in builders, 'production-release-verification-policy-incomplete')
        source = ProtectedSource(self.github)
        publication = None
        for repo, sha in ((PRODUCER, c.producer_sha), (MANAGER, c.manager_sha)):
            run = source.collect(repo, sha, [p for p in self.checks if p.repository == repo],
                                 self.publication_check if repo == PRODUCER else None,
                                 self.signer_sha, self.workflow)
            if repo == PRODUCER:
                publication = run
        manifest_hash = self._content_hash(self.manifest_path)
        policy_hash = self._content_hash('policy/admission.yaml')
        require((manifest_hash, policy_hash) == (c.manifest_hash, c.policy_hash),
                'production-release-manager-content-mismatch')
        blobs = self.registry.collect(c.image)
        verify_oci_bytes(c.image, IMAGE_REPOSITORY, blobs)
        doc = self.github.get(PRODUCER, '/attestations/' + c.image.split('@')[1] + '?per_page=100')
        attestations = doc['attestations']
        require(isinstance(attestations, list) and 0 < len(attestations) <= 10,
                'production-release-attestation-unavailable')
        # No bundle_url dereference: only embedded bundles from this exact repo.
        bundles = [a['bundle'] for a in attestations]
        identity = {'signerWorkflow': self.workflow, 'signerSha': self.signer_sha,
                    'sourceSha': c.producer_sha, 'sourceRef': 'refs/heads/main'}
        origin = {'sourceRepository': PRODUCER, 'sourceSha': c.producer_sha, 'sourceRef': 'refs/heads/main',
                  'platform': 'linux/amd64', 'buildArguments': {'VITE_SITE_ORIGIN': ORIGIN},
                  'originOutputVerified': True, 'runId': publication[0], 'runAttempt': publication[1]}
        expectations = ({'predicateType': SLSA}, {'predicateType': BUILD_ORIGIN, 'expectedPredicate': origin})
        # crypto returns statements only after pinned gh succeeds; unsigned API
        # JSON can never be interpreted as verifier stdout at this boundary.
        statements = self.crypto.verify(blobs[c.image.split('@')[1]], c.image, identity, expectations, bundles)
        require(isinstance(statements, tuple) and len(statements) == 2,
                'production-release-signature-unverified')
        slsa = statements[0]
        require(slsa['predicateType'] == SLSA and
                slsa['predicate']['runDetails']['builder']['id'] == self.slsa_builder,
                'production-release-builder-mismatch')
        return ReleaseFacts(((PRODUCER, c.producer_sha), (MANAGER, c.manager_sha)),
            tuple((p.repository, c.producer_sha if p.repository == PRODUCER else c.manager_sha, p.name, p.app_id)
                  for p in self.checks),
            (PRODUCER, c.producer_sha, 'refs/heads/main', c.image),
            (c.image, PRODUCER, c.producer_sha, self.builder), ORIGIN, manifest_hash, policy_hash, c.identity)
