"""Read-only historical Caddy release QA; never production authorization.

PR code is unreviewed. This command verifies immutable artifact/signature claims
with the production artifact contract, but deliberately cannot satisfy policy B,
operator approvals or any deployment gate. No Docker, publication or App token.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.parse

from scripts.application_acceptance import provision_gh
from scripts.caddy_producer import IMAGE, INPUTS, REPOSITORY, load_lock
from scripts.docker_adapter import DockerError
from scripts.production_integration.caddy_release import verify_caddy_artifact
from scripts.production_integration.release_crypto import AttestationVerifier, bounded_process
from scripts.production_integration.release_transport import GitHubRead, GHCRRead, HTTPSRead, require
from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths

SOURCE_SHA = '3d24ca2cefb723d8c8796940691bbbe31b19c1b2'
PUBLICATION = ('40024294624', '1')
DIGEST = 'sha256:cd65a6b7daa8029c9f367dcacb9dab14a5eb016c7e584e3ca7f1dbed89ed3800'
ATTESTATIONS = frozenset(('59250326', '59250361'))


class HistoricalAttestations:
    """Select the two historical embedded bundles; never follow bundle URLs."""
    def __init__(self, github):
        self.github = github

    def get(self, repository, path):
        require(repository == REPOSITORY and path == '/attestations/' + DIGEST + '?per_page=100',
                'caddy-qa-request-rejected')
        documents = self.github.get(repository, path)['attestations']
        require(isinstance(documents, list) and 0 < len(documents) <= 10,
                'caddy-qa-attestations-mismatch')
        selected = {}
        for document in documents:
            # IDs are API metadata only. The embedded bundle still has to pass
            # pinned-root gh verification and every signed claim below.
            name = urllib.parse.urlsplit(document['bundle_url']).path.rsplit('/', 1)[-1]
            identity = name.removesuffix('.json.sn')
            if identity in ATTESTATIONS:
                require(identity not in selected, 'caddy-qa-attestations-mismatch')
                selected[identity] = {'bundle': document['bundle']}
        require(set(selected) == ATTESTATIONS, 'caddy-qa-attestations-mismatch')
        return {'attestations': list(selected.values())}


def qa_runtime():
    # Rendering is pure; these paths are never accessed or created by QA.
    paths = RuntimePaths('/srv/portfolio/state', '/srv/portfolio/locks', '/srv/portfolio/journal',
                         '/srv/portfolio/caddy-config', '/srv/portfolio/certificates',
                         '/srv/portfolio/caddy-data', 104857600)
    return CaddyRuntime(IMAGE + '@' + DIGEST, load_lock()['version'], paths)


def historical_input_lock():
    # Exact bytes from SOURCE_SHA, independent of today's producer baseline.
    # Never used by the production collector or selected from API/env claims.
    raw = (Path(__file__).resolve().parents[1] / 'tests/fixtures/caddy/published-inputs.lock.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() ==
            'bfa5bdda95483f423b8fa65ffc429f3a0db0e3d2387e13b378887e2760294386', 'caddy-release-inputs-mismatch')
    return json.loads(raw)


def check_release():
    with tempfile.TemporaryDirectory(prefix='pd-caddy-qa-') as temporary:
        directory = Path(temporary)
        def execute(args, **kwargs):
            return bounded_process(args, directory, **kwargs)
        binary = Path(provision_gh(directory, execute=execute))
        lock = load_lock()
        crypto = AttestationVerifier(binary, hashlib.sha256(binary.read_bytes()).hexdigest(),
            INPUTS / 'github-trusted-root.jsonl', lock['githubTrustRootSha256'], repository=REPOSITORY)
        transport = HTTPSRead()
        github = HistoricalAttestations(GitHubRead(os.environ.get('GH_TOKEN'), transport))
        registry = GHCRRead(transport, repository=IMAGE)  # Anonymous public pull only.
        return verify_caddy_artifact(qa_runtime(), SOURCE_SHA, PUBLICATION,
                                     github=github, registry=registry, crypto=crypto, input_lock=historical_input_lock())


# Exact allowlist: no remote text, URLs, full API responses or credentials.
FAILURE_CODES = frozenset((
    'caddy-qa-request-rejected', 'caddy-qa-attestations-mismatch',
    'caddy-release-subject-invalid', 'caddy-release-version-mismatch',
    'caddy-release-attestation-unavailable', 'caddy-release-signature-unverified',
    'caddy-release-signed-subject-mismatch', 'caddy-release-builder-mismatch',
    'caddy-release-provenance-mismatch', 'caddy-release-inputs-mismatch',
    'production-release-token-unavailable', 'production-release-api-authentication-failed',
    'production-release-api-denied-or-rate-limited', 'production-release-api-not-found',
    'production-release-api-unavailable', 'production-release-api-timeout-or-unavailable',
    'production-release-api-pagination-unproven',
    'production-release-signature-unverified', 'production-release-signed-claims-mismatch',
    'production-release-crypto-installation-invalid', 'production-release-crypto-version-invalid',
    'production-release-crypto-timeout', 'production-release-crypto-unavailable',
    'production-release-oci-digest-mismatch', 'production-release-platform-unavailable',
    'caddy-entrypoint-command-user-or-filesystem-contract-mismatch',
))


def main():
    try:
        check_release()
    except Exception as error:
        code = str(error) if isinstance(error, DockerError) and str(error) in FAILURE_CODES else 'caddy-qa-failed'
        print('FAIL digest=' + DIGEST + ' code=' + code + ' productionAuthorized=false')
        return 1
    print('PASS digest=' + DIGEST + ' code=ok productionAuthorized=false protectedSource=not-verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
