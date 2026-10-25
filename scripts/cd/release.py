"""Authenticated image selection and a narrow projection of the sealed manifest."""
import base64
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile

from scripts.cd.policy import pin
from scripts.production_integration.artifacts import blob_digest, verify_oci_bytes
from scripts.production_integration.observations import require
from scripts.production_integration.release import PRODUCER, MANAGER, ORIGIN
from scripts.production_integration.release_collector import AuthenticatedReleaseCollector, BUILD_ORIGIN, SLSA
from scripts.production_integration.release_source import ProtectedSource, WorkflowCheck, PRODUCTION_REVIEW_POLICY
from scripts.reconciliation import DesiredApplication, DesiredState, encode, normalized_spec
from scripts.validate_registry import load_document, validate_registry

CONTENT = 'https://github.com/' + PRODUCER + '/attestations/public-content/v1'
WORKFLOW = '.github/workflows/publish.yml'
MANIFEST = 'registry/applications/project-router.yaml'


def source_file(github, repository, path, revision):
    require(re.fullmatch(r'[0-9a-f]{40}', revision), 'cd-source-sha-invalid')
    doc = github.get(repository, '/contents/' + path + '?ref=' + revision)
    require(doc['type'] == 'file' and doc['path'] == path and doc['encoding'] == 'base64' and
            type(doc['size']) is int and 0 < doc['size'] <= 65536, 'cd-source-file-invalid')
    raw = base64.b64decode(doc['content'].replace('\n', ''), validate=True)
    require(len(raw) == doc['size'], 'cd-source-file-incomplete')
    return raw


@dataclass(frozen=True)
class ImageRelease:
    source_sha: str
    image: str
    html_sha256: str
    run_id: str
    run_attempt: str

    def validate(self):
        require(re.fullmatch(r'[0-9a-f]{40}', self.source_sha) and
                re.fullmatch(r'ghcr.io/danilfaritovich/project-router@sha256:[0-9a-f]{64}', self.image) and
                re.fullmatch(r'[0-9a-f]{64}', self.html_sha256) and
                all(type(v) is str and re.fullmatch(r'[1-9][0-9]{0,19}', v) for v in (self.run_id, self.run_attempt)),
                'cd-image-release-invalid')
        return self


def require_forward_release(previous, target, github):
    """Out-of-order completed workflows must never downgrade a healthy source."""
    previous.validate()
    target.validate()
    if previous.source_sha == target.source_sha:
        return target
    comparison=github.get(PRODUCER,'/compare/'+previous.source_sha+'...'+target.source_sha)
    require(comparison['status']=='ahead' and comparison['base_commit']['sha']==previous.source_sha and
            comparison['merge_base_commit']['sha']==previous.source_sha,'cd-application-source-downgrade-forbidden')
    return target


class ImageReleaseVerifier:
    def __init__(self, policy, github, registry, crypto):
        self.policy, self.github, self.registry, self.crypto = policy, github, registry, crypto

    def verify(self, release):
        release.validate()
        p = self.policy.current().document['application']
        require(pin(source_file(self.github, PRODUCER, WORKFLOW, release.source_sha)) == p['workflow_sha256'],
                'cd-producer-workflow-change-requires-review')
        source = ProtectedSource(self.github, review_policy=PRODUCTION_REVIEW_POLICY)
        publication = source.collect(PRODUCER, release.source_sha, [WorkflowCheck(**c) for c in p['checks']],
                                     WorkflowCheck(**p['publication']), release.source_sha, WORKFLOW)
        require(tuple(publication) == (release.run_id, release.run_attempt), 'cd-publication-attempt-mismatch')
        blobs = self.registry.collect(release.image)
        verify_oci_bytes(release.image, 'ghcr.io/danilfaritovich/project-router', blobs)
        docs = self.github.get(PRODUCER, '/attestations/' + release.image.split('@')[1] + '?per_page=100')['attestations']
        require(type(docs) is list and 0 < len(docs) <= 10, 'cd-attestations-incomplete')
        identity = {'signerWorkflow': WORKFLOW, 'signerSha': release.source_sha,
                    'sourceSha': release.source_sha, 'sourceRef': 'refs/heads/main'}
        origin = {'sourceRepository': PRODUCER, 'sourceSha': release.source_sha, 'sourceRef': 'refs/heads/main',
                  'platform': 'linux/amd64', 'buildArguments': {'VITE_SITE_ORIGIN': ORIGIN},
                  'originOutputVerified': True, 'runId': release.run_id, 'runAttempt': release.run_attempt}
        content = {**{k: origin[k] for k in ('sourceRepository', 'sourceSha', 'sourceRef', 'runId', 'runAttempt')},
                   'imageDigest': release.image.split('@')[1], 'path': '/en/', 'htmlSha256': release.html_sha256}
        statements = self.crypto.verify(blobs[release.image.split('@')[1]], release.image, identity,
            ({'predicateType': SLSA, 'expectedInvocation': 'https://github.com/' + PRODUCER + '/actions/runs/' +
              release.run_id + '/attempts/' + release.run_attempt}, {'predicateType': BUILD_ORIGIN, 'expectedPredicate': origin},
             {'predicateType': CONTENT, 'expectedPredicate': content}), [a['bundle'] for a in docs])
        require(len(statements) == 3 and statements[0]['predicate']['runDetails']['builder']['id'] == p['slsa_builder'],
                'cd-builder-untrusted')
        self.policy.current()
        return release


class ProjectedContent:
    """The only mutable fields are one image and authenticated release claims.

    The existing complete registry/admission validator is the authority for the
    effective document. No production tree is written or patched in place.
    """
    def __init__(self, installation, policy, release, *, root=None):
        from scripts.production_integration.installation import BUNDLE
        self.installation, self.policy, self.release = installation, policy, release.validate()
        self.root = BUNDLE / 'manager' if root is None else Path(root)

    def __call__(self):
        self.installation.verify()
        self.policy.current()
        template = (self.root / MANIFEST).read_bytes()
        require(pin(template) == self.policy.document['application']['template_sha256'], 'cd-template-changed')
        doc = load_document(template)
        require(doc['metadata']['id'] == 'project-router' and len(doc['spec']['components']) == 1 and
                doc['spec']['components'][0]['name'] == 'web' and doc['spec']['storage'] == {'type': 'stateless'},
                'cd-stateless-contract-required')
        doc = deepcopy(doc)
        r = self.release
        doc['spec']['components'][0]['image'] = r.image
        contract = doc['release']['components']['web']
        contract['sourceSha'] = contract['signerSha'] = r.source_sha
        for attestation in contract['attestations']:
            if attestation['predicateType'] == BUILD_ORIGIN:
                attestation['expectedPredicate'].update(sourceSha=r.source_sha, runId=r.run_id, runAttempt=r.run_attempt)
        raw = encode(doc).encode()
        policy = (self.root / 'policy/admission.yaml').read_bytes()
        with tempfile.TemporaryDirectory(prefix='pd-cd-admission-') as temporary:
            root = Path(temporary)
            (root / 'registry/applications').mkdir(parents=True)
            (root / 'policy').mkdir()
            (root / MANIFEST).write_bytes(raw)
            (root / 'policy/admission.yaml').write_bytes(policy)
            registry = validate_registry(root)
        require(len(registry.applications) == 1, 'cd-admission-scope-invalid')
        app = registry.applications[0]
        desired = DesiredState(self.installation.manager_sha, (DesiredApplication(
            app['metadata']['id'], app['metadata']['sourceRepository'], encode(normalized_spec(app['spec']))),))
        return desired, raw, policy

    def verify_source_binding(self, candidate, template_hash, policy_hash):
        _, raw, admission = self()
        require(template_hash == 'sha256:' + self.policy.document['application']['template_sha256'] and
                policy_hash == blob_digest(admission) == candidate.policy_hash and
                self.release.image == candidate.image and self.release.source_sha == candidate.producer_sha,
                'cd-release-projection-source-mismatch')
        return blob_digest(raw)
