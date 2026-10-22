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


def verify_oci_bytes(reference, allowed_repository, blobs):
    """Verify only OCI bytes/platform mapping; establishes no source/release trust.

    Shared by isolated acceptance and the production verifier. Callers must never
    treat this result as production source admission.
    """
    if not allowed_repository.startswith('ghcr.io/'):
        raise DockerError('artifact-registry-not-admitted')
    return map_oci_bytes(reference, allowed_repository, blobs)


def map_oci_bytes(reference, repository, blobs):
    """Registry-independent byte mapper; grants no admission or source trust."""
    if not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', reference):
        raise DockerError('artifact-repository-or-digest-invalid')
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
    return ImageEvidence(reference, config['digest'], 'linux/amd64', selected, True, encode(document.get('config') or {}))


def verify_artifact(reference, allowed_repository, blobs, source, verifier=None, *, expected_source, expected_origin=None):
    """OCI mapping plus the separately authenticated full production source gate."""
    if (source.repository != expected_source or source.build_origin != expected_origin or
        source.manager_repository != 'DanilFaritovich/portfolio-deployment'):
        raise DockerError('source-repository-or-build-origin-mismatch')
    evidence = verify_oci_bytes(reference, allowed_repository, blobs)
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
    return evidence


class EngineBindingError(DockerError):
    """Only fixed codes and structural categories; never arbitrary Config data."""
    def __init__(self, codes, checks):
        super().__init__('engine-artifact-mapping-mismatch')
        self.codes = codes
        self.checks = checks


# Field names are allowlisted: image labels, env entries and unknown config keys
# can contain private data. Never copy those names or any values into reports.
CONFIG_FIELDS = ('User', 'Env', 'Entrypoint', 'Cmd', 'WorkingDir', 'ExposedPorts',
                 'Volumes', 'Labels', 'StopSignal', 'Healthcheck', 'ArgsEscaped',
                 'Shell', 'OnBuild', 'StopTimeout', 'Hostname', 'Domainname',
                 'AttachStdin', 'AttachStdout', 'AttachStderr', 'Tty', 'OpenStdin',
                 'StdinOnce', 'Image', 'NetworkDisabled', 'MacAddress')


def normalize_engine_config(config):
    """Compare a bounded image Config projection, never container Config.

    Moby's container.Config uses non-omitempty strings/bools and nil map/slice
    fields: https://github.com/moby/moby/blob/v27.5.1/api/types/container/config.go
    encoding/json materializes absent strings/bools as Go zero values and nil
    maps/slices as null. The 11 fields observed in run 39828163158 plus empty User and null
    Entrypoint from Caddy run 39843536752 are normalized. No empty map/list equivalence is assumed.
    OCI 1.1.1 config.md permits null for optional properties; all other fields
    retain exact presence, JSON type and value, including nonempty User and Healthcheck.

    Unknown fields/types fail closed even if both sides contain them. This is
    intentionally narrower than every representation accepted by Moby.
    """
    if not isinstance(config, dict) or config.keys() - set(CONFIG_FIELDS):
        raise ValueError('unsupported-image-config')
    strings = {'User', 'WorkingDir', 'StopSignal', 'Hostname', 'Domainname', 'Image', 'MacAddress'}
    arrays = {'Env', 'Entrypoint', 'Cmd', 'Shell', 'OnBuild'}
    booleans = {'ArgsEscaped', 'AttachStdin', 'AttachStdout', 'AttachStderr',
                'Tty', 'OpenStdin', 'StdinOnce', 'NetworkDisabled'}
    for key, value in config.items():
        if value is None:
            continue  # Preserve null, except the explicit defaults listed below.
        valid = False
        if key in strings:
            valid = isinstance(value, str)
        elif key in arrays:
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif key in booleans:
            valid = type(value) is bool
        elif key in {'Volumes', 'ExposedPorts', 'Labels'}:
            valid = isinstance(value, dict) and all(
                isinstance(k, str) and (isinstance(v, str) if key == 'Labels' else type(v) is dict and not v)
                for k, v in value.items())
        elif key == 'StopTimeout':
            valid = type(value) is int
        elif key == 'Healthcheck':
            valid = isinstance(value, dict) and all(
                (k == 'Test' and isinstance(v, list) and all(isinstance(arg, str) for arg in v)) or
                (k in {'Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries'} and type(v) is int)
                for k, v in value.items())
        if not valid:
            raise ValueError('unsupported-image-config')
    result = dict(config)
    # These are image metadata defaults, never permission to ignore nonzero data.
    defaults = {'Hostname': '', 'Domainname': '', 'Image': '',
                'AttachStdin': False, 'AttachStdout': False, 'AttachStderr': False,
                'Tty': False, 'OpenStdin': False, 'StdinOnce': False,
                'Volumes': None, 'OnBuild': None, 'User': '', 'Entrypoint': None}
    for key, default in defaults.items():
        if key in result and encode(result[key]) == encode(default):
            del result[key]
    return result


def config_shape(config, key):
    """Fixed structural categories only: no values, sizes or nested keys."""
    if key not in config:
        return 'absent'
    value = config[key]
    if value is None:
        return 'null'
    if type(value) is bool:
        return 'true' if value else 'false'
    for kind, name in ((str, 'string'), (list, 'array'), (dict, 'object')):
        if isinstance(value, kind):
            return name if value else 'empty-' + name
    return 'other'


def bind_engine_image(evidence, inspected):
    """Registry verification plus fresh Engine evidence, never RepoDigests alone.

    Preserve strict binding with bounded representation compatibility. A config
    digest authenticates raw registry bytes; inspect Config is an API projection,
    not those bytes. No diagnostic observation grants identity or runtime trust.
    """
    if not isinstance(inspected, dict):
        raise EngineBindingError(('engine-inspect-invalid',), {})
    digests = inspected.get('RepoDigests')
    valid_digests = isinstance(digests, list) and all(isinstance(d, str) for d in digests)
    manifest_ref = evidence.reference.split('@')[0] + '@' + evidence.manifest_digest
    config = inspected.get('Config')
    expected = parse_snapshot(evidence.defaults_json.encode())
    try:
        config_matches = encode(normalize_engine_config(config)) == encode(normalize_engine_config(expected))
    except ValueError:
        config_matches = False
    checks = {
        'imageIdMatchesConfig': inspected.get('Id') == evidence.image_id,
        'imageIdMatchesManifest': inspected.get('Id') == evidence.manifest_digest,
        'imageIdMatchesRequestedDigest': inspected.get('Id') == evidence.reference.split('@')[1],
        'repoDigestsValid': valid_digests,
        'repoDigestsContainRequested': valid_digests and evidence.reference in digests,
        'repoDigestsContainPlatformManifest': valid_digests and manifest_ref in digests,
        'platformMatches': inspected.get('Os') == 'linux' and inspected.get('Architecture') == 'amd64',
        'configMatches': config_matches,
    }
    if isinstance(config, dict) and isinstance(expected, dict):
        # Keep PR #19's raw differences; describe shapes before normalization.
        missing = object()
        differing = {k for k in config.keys() | expected.keys()
                     if encode(config.get(k, None)) != encode(expected.get(k, None)) or
                     (config.get(k, missing) is missing) != (expected.get(k, missing) is missing)}
        checks['configDifferingFields'] = [k for k in CONFIG_FIELDS if k in differing]
        checks['configOtherFieldsDiffer'] = bool(differing - set(CONFIG_FIELDS))
        checks['configNullOrOmittedOnly'] = bool(differing) and all(
            config.get(k) is None and expected.get(k) is None for k in differing)
        checks['configDifferingShapes'] = {
            k: {'oci': config_shape(expected, k), 'engine': config_shape(config, k)}
            for k in CONFIG_FIELDS if k in differing}
    codes = tuple(code for key, code in (
        ('imageIdMatchesConfig', 'engine-image-id-mismatch'),
        ('repoDigestsContainRequested', 'engine-repo-digests-mismatch'),
        ('platformMatches', 'engine-platform-mismatch'),
        ('configMatches', 'engine-config-mismatch'),
    ) if not checks[key])
    if codes:
        raise EngineBindingError(codes, checks)
    return evidence
