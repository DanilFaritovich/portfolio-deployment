"""Offline OCI byte verification; external source trust is a separate injected port.

No registry credentials or network client. Labels never establish provenance.
"""
from dataclasses import dataclass
import hashlib
import re

from scripts.docker_adapter import DockerError
from scripts.docker_discovery import ImageEvidence, MANIFEST_TYPES
from scripts.planning_inputs import parse_snapshot
from scripts.reconciliation import encode, revision

INDEX_TYPES = {'application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'}


def blob_digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SourceRequest:
    repository: str
    producer_revision: str
    manager_revision: str
    reference: str
    build_origin: str | None
    manager_repository: str = "DanilFaritovich/portfolio-deployment"


@dataclass(frozen=True)
class SourceVerification:
    """Result of an independently trusted verifier, never parsed from user booleans.

    The verifier must authenticate protected main ancestry/branch rules for BOTH
    repositories, exact-SHA CI and successful publish, provenance subject digest,
    builder identity/workflow and build args. Missing capability is a blocker.
    """
    request: SourceRequest
    blockers: tuple[str, ...]
    evidence_identity: str


def verify_artifact(reference, allowed_repository, blobs, source, verifier=None, *, expected_source, expected_origin=None):
    """blobs: digest -> raw registry bytes (bounded, duplicate-key rejecting JSON).

    Supports one index level and exactly one linux/amd64 descriptor. Verifies every
    selected descriptor's digest, byte size and media type; config ID is NOT the
    manifest digest. Does not assert network availability beyond supplied bytes.
    """
    if not re.fullmatch(re.escape(allowed_repository) + r'@sha256:[0-9a-f]{64}', reference):
        raise DockerError('artifact-repository-or-digest-invalid')
    if not allowed_repository.startswith('ghcr.io/'):
        raise DockerError('artifact-registry-not-admitted')
    if (source.repository != expected_source or source.build_origin != expected_origin or
        source.manager_repository != 'DanilFaritovich/portfolio-deployment'):
        raise DockerError('source-repository-or-build-origin-mismatch')
    def descriptor_valid(descriptor, media_types, *, bounded=True):
        return (isinstance(descriptor, dict) and descriptor.get('mediaType') in media_types and
                isinstance(descriptor.get('digest'), str) and
                re.fullmatch(r'sha256:[0-9a-f]{64}', descriptor['digest']) is not None and
                type(descriptor.get('size')) is int and descriptor['size'] > 0 and
                (not bounded or descriptor['size'] <= 1024 * 1024))

    def read(digest, size=None):
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise DockerError('artifact-descriptor-digest-invalid')
        raw = blobs.get(digest)
        if (type(raw) is not bytes or len(raw) > 1024 * 1024 or blob_digest(raw) != digest or
            (size is not None and (type(size) is not int or len(raw) != size))):
            raise DockerError('artifact-bytes-unavailable-or-digest-mismatch')
        try:
            doc = parse_snapshot(raw)
            if not isinstance(doc, dict):
                raise ValueError
            return doc
        except (ValueError, TypeError, UnicodeError):
            raise DockerError('artifact-json-invalid') from None
    selected = reference.split('@')[1]
    manifest = read(selected)
    if type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 2:
        raise DockerError('artifact-schema-version-invalid')
    if manifest.get('mediaType') in INDEX_TYPES:
        descriptors = manifest.get('manifests')
        if not isinstance(descriptors, list) or len(descriptors) > 64:
            raise DockerError('artifact-index-invalid')
        matches = [d for d in descriptors if isinstance(d, dict) and d.get('platform') == {'os': 'linux', 'architecture': 'amd64'}]
        if len(matches) != 1:
            raise DockerError('artifact-platform-ambiguous-or-unavailable')
        descriptor = matches[0]
        if not descriptor_valid(descriptor, MANIFEST_TYPES):
            raise DockerError('artifact-platform-manifest-invalid')
        selected = descriptor.get('digest')
        manifest = read(selected, descriptor['size'])
        if manifest.get('mediaType') != descriptor['mediaType']:
            raise DockerError('artifact-media-type-mismatch')
    if (manifest.get('mediaType') not in MANIFEST_TYPES or type(manifest.get('schemaVersion')) is not int or
        manifest['schemaVersion'] != 2):
        raise DockerError('artifact-manifest-unsupported')
    config = manifest.get('config') or {}
    if not descriptor_valid(config, {'application/vnd.oci.image.config.v1+json', 'application/vnd.docker.container.image.v1+json'}):
        raise DockerError('artifact-config-unsupported')
    layers = manifest.get('layers')
    layer_types = {'application/vnd.oci.image.layer.v1.tar', 'application/vnd.oci.image.layer.v1.tar+gzip',
                   'application/vnd.oci.image.layer.v1.tar+zstd', 'application/vnd.docker.image.rootfs.diff.tar.gzip'}
    if not isinstance(layers, list) or len(layers) > 128 or any(
            not descriptor_valid(layer, layer_types, bounded=False) for layer in layers):
        raise DockerError('artifact-layers-unsupported')
    document = read(config.get('digest'), config['size'])
    if document.get('os') != 'linux' or document.get('architecture') != 'amd64':
        raise DockerError('artifact-config-platform-mismatch')
    try:
        revision(source.producer_revision)
        revision(source.manager_revision)
    except (ValueError, AttributeError):
        raise DockerError('source-revision-unverified') from None
    if source.reference != reference:
        raise DockerError('source-subject-mismatch')
    if verifier is None:
        raise DockerError('source-protected-main-ci-provenance-build-origin-verifier-unavailable')
    proof = verifier.verify(source)
    if (not isinstance(proof, SourceVerification) or proof.request != source or proof.blockers or
        not re.fullmatch(r'sha256:[0-9a-f]{64}', proof.evidence_identity)):
        raise DockerError('source-evidence-unverified')
    return ImageEvidence(reference, config['digest'], 'linux/amd64', selected, True, encode(document.get('config') or {}))


def bind_engine_image(evidence, inspected):
    """Registry verification plus fresh Engine evidence, never RepoDigests alone."""
    if (inspected.get('Id') != evidence.image_id or evidence.reference not in inspected.get('RepoDigests', []) or
        inspected.get('Os') != 'linux' or inspected.get('Architecture') != 'amd64' or
        encode(inspected.get('Config') or {}) != evidence.defaults_json):
        raise DockerError('engine-artifact-mapping-mismatch')
    return evidence
