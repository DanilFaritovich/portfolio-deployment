"""Separate read-only Caddy provenance composition over existing trusted ports.

No ProjectRouter candidate, cache preparation, authorization or deployment scope.
Only installed code may supply the GitHub/registry/crypto ports and check policy.
"""
import re

from scripts.caddy_producer import IMAGE, REPOSITORY, WORKFLOW, PREDICATE, LEGACY_PREDICATE, SLSA, BUILDER, published_inputs, load_lock
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import verify_oci_bytes
from scripts.production_integration.caddy_image import inspect_caddy_contract
from scripts.production_integration.release_source import ProtectedSource, PRODUCTION_REVIEW_POLICY
from scripts.production_integration.release_transport import require


HISTORICAL_RELEASE = ('3d24ca2cefb723d8c8796940691bbbe31b19c1b2',
    IMAGE + '@sha256:cd65a6b7daa8029c9f367dcacb9dab14a5eb016c7e584e3ca7f1dbed89ed3800',
    ('40024294624', '1'))
# Frozen published v1 inputs, independent of future producer lock updates.
HISTORICAL_INPUTS = {'version': '2.11.7',
 'sourceSha': '72dd0fb067f6d7826c7f79907670ba4a713bfe37',
 'archiveSha256': '727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b',
 'certificateSha256': '43406c10323f8c71e39e096e19fe2cf490cb46461c2c18c58d7a64eabf4601c0',
 'signatureSha256': 'c91d7fd9cd7e5655f9a94db7d40e82b22b0f4f6b257831b4b7e33a33e1c4c705',
 'binarySha256': '678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9',
 'baseIndex': 'sha256:034df5c1737e8241f6ed6423a12e720ee0f6d6f088609ee172f98f6f2166ef5d',
 'baseManifest': 'sha256:5e858517feb24e8b721c7d0ab6d48776ce42140d66ec6ce35618d31cad5a25ff',
 'cosignVersion': '2.6.1',
 'cosignSha256': '064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a',
 'dockerVersion': '28.0.4',
 'buildxVersion': '0.38.0',
 'composeVersion': '2.38.2',
 'runnerImageVersion': '20261012.334.1',
 'githubTrustRootSha256': '65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c'}


def artifact_input_policy(runtime, source_sha, publication):
    if (source_sha, runtime.image, publication) == HISTORICAL_RELEASE:
        return dict(HISTORICAL_INPUTS)
    return load_lock()


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
        return verify_caddy_artifact(runtime, source_sha, publication, github=self.github,
                                     registry=self.registry, crypto=self.crypto)


def verify_caddy_artifact(runtime, source_sha, publication, *, github, registry, crypto, input_lock=None):
    """Immutable artifact/signature contract only; grants no protected-source trust.

    Production calls this only after full ProtectedSource verification. PR QA
    calls it with a fixed historical release/lock and never constructs release facts.
    The production collector never supplies an alternate input_lock.
    """
    require(isinstance(source_sha, str) and re.fullmatch(r'[0-9a-f]{40}', source_sha) is not None and
            re.fullmatch(re.escape(IMAGE) + r'@sha256:[0-9a-f]{64}', runtime.image) is not None,
            'caddy-release-subject-invalid')
    # Compatibility is installed policy, never chosen by unsigned bundle claims.
    lock = artifact_input_policy(runtime, source_sha, publication) if input_lock is None else input_lock
    predicate_type = LEGACY_PREDICATE if 'runnerImageVersion' in lock else PREDICATE
    require(runtime.version == lock['version'], 'caddy-release-version-mismatch')
    blobs = registry.collect(runtime.image)
    evidence = verify_oci_bytes(runtime.image, IMAGE, blobs)
    inspect_caddy_contract(runtime, evidence)
    attestations = github.get(REPOSITORY, '/attestations/' + runtime.image.split('@')[1] +
                                   '?per_page=100')['attestations']
    require(isinstance(attestations, list) and 0 < len(attestations) <= 10,
            'caddy-release-attestation-unavailable')
    identity = {'signerWorkflow': WORKFLOW, 'signerSha': source_sha,
                'sourceSha': source_sha, 'sourceRef': 'refs/heads/main', 'signerIdentity': BUILDER}
    expected = published_inputs(lock, source_sha, *publication)
    statements = crypto.verify(blobs[runtime.image.split('@')[1]], runtime.image, identity,
        ({'predicateType': SLSA}, {'predicateType': predicate_type, 'expectedPredicate': expected}),
        [a['bundle'] for a in attestations])
    require(isinstance(statements, tuple) and len(statements) == 2, 'caddy-release-signature-unverified')
    for statement, kind in zip(statements, (SLSA, predicate_type)):
        require(statement.get('_type') == 'https://in-toto.io/Statement/v1' and
                statement.get('predicateType') == kind and statement.get('subject') ==
                [{'name': IMAGE, 'digest': {'sha256': runtime.image.split('@sha256:')[1]}}],
                'caddy-release-signed-subject-mismatch')
    require(statements[0]['predicate']['runDetails']['builder']['id'] == BUILDER,
            'caddy-release-builder-mismatch')
    slsa = statements[0]['predicate']
    definition = slsa['buildDefinition']
    require(definition['buildType'] == 'https://actions.github.io/buildtypes/workflow/v1' and
            definition['externalParameters']['workflow'] == {
                'path': '.github/workflows/caddy-image.yml', 'ref': 'refs/heads/main',
                'repository': 'https://github.com/' + REPOSITORY} and
            definition['resolvedDependencies'] == [{
                'uri': 'git+https://github.com/' + REPOSITORY + '@refs/heads/main',
                'digest': {'gitCommit': source_sha}}] and
            slsa['runDetails']['metadata']['invocationId'] == 'https://github.com/' + REPOSITORY +
                '/actions/runs/' + publication[0] + '/attempts/' + publication[1],
            'caddy-release-provenance-mismatch')
    require(statements[1]['predicate'] == expected, 'caddy-release-inputs-mismatch')
    return evidence  # Artifact evidence alone grants no production authority.
