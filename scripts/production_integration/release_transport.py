"""Bounded HTTPS reads. Credentials are confined to their exact origin.

No proxy, retries, mutation or environment credential lookup. Only a fixed
credential-free GHCR blob CDN redirect is allowed.
One transport instance is scoped to one collection (120 seconds / 64 requests).
"""
import base64
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.docker_adapter import DockerError
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.release import PRODUCER, MANAGER

LIMIT = 1024 * 1024
IMAGE_REPOSITORY = 'ghcr.io/danilfaritovich/project-router'


def require(condition, code='production-release-api-incomplete'):
    if not condition:
        raise DockerError(code)


def valid_bearer_token(token):
    # Credentials are opaque, including stateless ghs_APPID_JWT tokens. Check
    # only bounded HTTP-header safety, never decode or authenticate a JWT here.
    return isinstance(token, str) and re.fullmatch(r'[!-~]{1,4096}', token) is not None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTPSRead:
    def __init__(self):
        self.deadline = time.monotonic() + 120
        self.remaining = 64
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, url, headers):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == 'https' and parsed.netloc in ('api.github.com', 'ghcr.io', 'pkg-containers.githubusercontent.com') and
                not parsed.fragment, 'production-release-host-rejected')
        if parsed.netloc == 'pkg-containers.githubusercontent.com':
            headers = {}
        self.remaining -= 1
        remaining = self.deadline - time.monotonic()
        require(self.remaining >= 0 and remaining > 0, 'production-release-budget-exhausted')
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers), timeout=min(10, remaining)) as response:
                require(response.status == 200, 'production-release-api-incomplete')
                raw = bytearray()
                while len(raw) <= LIMIT:
                    require(time.monotonic() < self.deadline, 'production-release-api-timeout')
                    chunk = response.read1(min(65536, LIMIT + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                require(len(raw) <= LIMIT, 'production-release-response-too-large')
                require(not response.headers.get('Link'), 'production-release-api-pagination-unproven')
                return bytes(raw)
        except urllib.error.HTTPError as error:
            # Only GHCR config/blob delivery may redirect, once, to the fixed
            # GitHub container CDN. Drop every credential before that request.
            location = error.headers.get('Location', '')
            target = urllib.parse.urlsplit(location)
            error.close()
            if (error.code in (302, 307) and parsed.netloc == 'ghcr.io' and
                '/blobs/sha256:' in parsed.path and target.scheme == 'https' and
                target.netloc == 'pkg-containers.githubusercontent.com' and not target.fragment):
                return self.get(location, {})
            if error.code == 401:
                raise DockerError('production-release-api-authentication-failed') from None
            if error.code in (403, 429):
                raise DockerError('production-release-api-denied-or-rate-limited') from None
            if error.code == 404:
                raise DockerError('production-release-api-not-found') from None
            raise DockerError('production-release-api-unavailable') from None
        except (TimeoutError, OSError):
            raise DockerError('production-release-api-timeout-or-unavailable') from None


class GitHubRead:
    """Authenticated fixed-repository REST reads, never response-provided URLs."""
    def __init__(self, token, transport):
        require(valid_bearer_token(token), 'production-release-token-unavailable')
        self._token, self.transport = token, transport

    def get(self, repository, path):
        require(repository in (PRODUCER, MANAGER) and
                (path == '' or re.fullmatch(r'/[A-Za-z0-9_./:?=&%-]+', path) is not None) and
                '..' not in path.split('/') and not path.startswith('//') and '%' not in path, 'production-release-api-path-rejected')
        try:
            result = parse_snapshot(self.transport.get('https://api.github.com/repos/' + repository + path, {
                'Authorization': 'Bearer ' + self._token, 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
            }))
            require(isinstance(result, (dict, list)))
            return result
        except (ValueError, TypeError, UnicodeError):
            raise DockerError('production-release-api-incomplete') from None


def bounded_items(document, key=None):
    values = document if key is None else document[key]
    require(isinstance(values, list) and len(values) < 100)
    if key is not None:
        require(type(document['total_count']) is int and document['total_count'] == len(values),
                'production-release-api-pagination-unproven')
    return values


class GHCRRead:
    def __init__(self, transport, *, repository=IMAGE_REPOSITORY, registry_auth=None):
        require(repository in (IMAGE_REPOSITORY, 'ghcr.io/danilfaritovich/portfolio-caddy'),
                'production-release-image-rejected')
        self.transport = transport
        self.repository = repository
        require(registry_auth is None or (repository == 'ghcr.io/danilfaritovich/portfolio-caddy' and
                type(registry_auth) is tuple and len(registry_auth) == 2 and
                all(isinstance(v, str) for v in registry_auth) and
                re.fullmatch(r'[A-Za-z0-9_\[\]-]{1,128}', registry_auth[0]) is not None and
                valid_bearer_token(registry_auth[1])),
                'production-release-registry-credential-invalid')
        self._registry_auth = registry_auth

    def collect(self, reference):
        # Anonymous pull by default. Explicit Caddy-only credentials reach only
        # the fixed GHCR token endpoint; no Docker credential-store discovery.
        from scripts.production_integration.artifacts import INDEX_TYPES, blob_digest, verify_oci_bytes
        require(re.fullmatch(re.escape(self.repository) + r'@sha256:[0-9a-f]{64}', reference) is not None,
                'production-release-image-rejected')
        path = self.repository.removeprefix('ghcr.io/')
        auth_headers = {} if self._registry_auth is None else {
            'Authorization': 'Basic ' + base64.b64encode(':'.join(self._registry_auth).encode()).decode()}
        token_doc = parse_snapshot(self.transport.get(
            'https://ghcr.io/token?service=ghcr.io&scope=repository:' + path + ':pull', auth_headers))
        token = token_doc['token']
        require(isinstance(token, str) and 0 < len(token) <= 16384 and '\n' not in token and '\r' not in token)
        headers = {'Authorization': 'Bearer ' + token, 'Accept': ', '.join(sorted(INDEX_TYPES | {
            'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}))}
        blobs = {}
        def read(digest, kind):
            require(isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is not None)
            raw = self.transport.get('https://ghcr.io/v2/' + path + '/' + kind + '/' + digest, headers)
            require(type(raw) is bytes and len(raw) <= LIMIT and blob_digest(raw) == digest,
                    'production-release-oci-digest-mismatch')
            blobs[digest] = raw
            return parse_snapshot(raw)
        manifest = read(reference.split('@')[1], 'manifests')
        if manifest.get('mediaType') in INDEX_TYPES:
            descriptors = manifest['manifests']
            require(isinstance(descriptors, list) and len(descriptors) <= 64)
            matches = [d for d in descriptors if isinstance(d, dict) and
                       d.get('platform') == {'os': 'linux', 'architecture': 'amd64'}]
            require(len(matches) == 1, 'production-release-platform-unavailable')
            manifest = read(matches[0]['digest'], 'manifests')
        read(manifest['config']['digest'], 'blobs')
        verify_oci_bytes(reference, self.repository, blobs)
        return blobs
