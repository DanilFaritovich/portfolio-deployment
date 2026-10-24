"""Explicit networked producer tooling; never imported by default offline checks.

Only prepare downloads public inputs. No deployment, secret material or build RUN.
The reviewed lock authenticates selection; cosign authenticates the publishers.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import blob_digest, map_oci_bytes
from scripts.production_integration.release_transport import require

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'images/caddy'
REPOSITORY = 'DanilFaritovich/portfolio-deployment'
IMAGE = 'ghcr.io/danilfaritovich/portfolio-caddy'
BASE = 'gcr.io/distroless/static-debian13'
UPSTREAM = 'https://github.com/caddyserver/caddy'
WORKFLOW = '.github/workflows/caddy-publish.yml'
PREDICATE = 'https://github.com/' + REPOSITORY + '/attestations/caddy-inputs/v1'
SLSA = 'https://slsa.dev/provenance/v1'
BUILDER = 'https://github.com/' + REPOSITORY + '/' + WORKFLOW + '@refs/heads/main'
KEYS = frozenset(('version', 'sourceSha', 'archiveSha256', 'certificateSha256', 'signatureSha256',
                  'binarySha256', 'baseIndex', 'baseManifest', 'cosignVersion', 'cosignSha256',
                  'githubTrustRootSha256', 'dockerVersion', 'buildxVersion', 'composeVersion', 'runnerImageVersion'))


def load_lock():
    lock = json.loads((INPUTS / 'inputs.lock.json').read_bytes())
    require(set(lock) == KEYS, 'caddy-input-lock-invalid')
    for key, value in lock.items():
        pattern = (r'[0-9]+\.[0-9]+\.[0-9]+' if key.endswith('Version') or key == 'version' else
                   r'[0-9a-f]{40}' if key == 'sourceSha' else
                   r'sha256:[0-9a-f]{64}' if key.startswith('base') else r'[0-9a-f]{64}')
        require(isinstance(value, str) and re.fullmatch(pattern, value) is not None, 'caddy-input-lock-invalid')
    require(blob_digest((INPUTS / 'github-trusted-root.jsonl').read_bytes()) ==
            'sha256:' + lock['githubTrustRootSha256'], 'caddy-trust-root-mismatch')
    recipe = (INPUTS / 'Dockerfile').read_text()
    require(recipe.splitlines()[0] == 'FROM ' + BASE + '@' + lock['baseIndex'] and
            'CADDY_VERSION=v' + lock['version'] in recipe, 'caddy-recipe-lock-mismatch')
    return lock


def read_public(url, limit=200 * 1024 * 1024):
    # Fixed callers construct public HTTPS URLs, never accept response URLs as policy.
    headers = {'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'} if (
        url.startswith('https://gcr.io/v2/')) else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        raw = response.read(limit + 1)
    require(0 < len(raw) <= limit, 'caddy-download-bound')
    return raw


def checked_download(url, digest, path):
    raw = read_public(url)
    require(hashlib.sha256(raw).hexdigest() == digest, 'caddy-input-digest-mismatch')
    path.write_bytes(raw)
    return raw


def run(args, *, env=None):
    # Public verifier diagnostics only. No inherited Docker/GitHub credentials.
    result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=180, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024,
            'caddy-input-signature-or-command-failed')
    return result.stdout


def upstream_command(binary, directory, lock):
    return [str(binary), 'verify-blob', '--certificate', str(directory / 'caddy.pem'),
            '--signature', str(directory / 'caddy.sig'),
            '--certificate-identity', UPSTREAM + '/.github/workflows/release.yml@refs/tags/v' + lock['version'],
            '--certificate-oidc-issuer', 'https://token.actions.githubusercontent.com',
            '--certificate-github-workflow-sha', lock['sourceSha'],
            '--certificate-github-workflow-repository', 'caddyserver/caddy',
            '--certificate-github-workflow-ref', 'refs/tags/v' + lock['version'],
            '--certificate-github-workflow-trigger', 'push', str(directory / 'caddy.tar.gz')]


def extract_binary(raw, lock, directory):
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = archive.getmembers()
        require(len(members) <= 10 and len({m.name for m in members}) == len(members), 'caddy-archive-invalid')
        for name, limit in (('caddy', 128 * 1024 * 1024), ('LICENSE', 65536)):
            selected = [m for m in members if m.name == name]
            require(len(selected) == 1 and selected[0].isfile() and 0 < selected[0].size <= limit,
                    'caddy-archive-invalid')
            data = archive.extractfile(selected[0]).read(limit + 1)
            if name == 'caddy':
                require(hashlib.sha256(data).hexdigest() == lock['binarySha256'] and
                        data[:6] == b'\x7fELF\x02\x01' and data[18:20] == b'\x3e\x00', 'caddy-binary-invalid')
                # ELF64 little-endian: reject dynamic interpreter dependencies.
                offset = int.from_bytes(data[32:40], 'little')
                size = int.from_bytes(data[54:56], 'little')
                count = int.from_bytes(data[56:58], 'little')
                require(size >= 56 and 0 < count <= 128 and offset + count * size <= len(data) and
                        all(int.from_bytes(data[offset + n * size:offset + n * size + 4], 'little') != 3
                            for n in range(count)), 'caddy-binary-not-static')
            (directory / name).write_bytes(data)
            (directory / name).chmod(0o755 if name == 'caddy' else 0o644)


def prepare(directory):
    lock = load_lock()
    directory.mkdir(mode=0o700)  # Fresh, never reuse untrusted/stale build inputs.
    context = directory / 'context'
    context.mkdir()
    home = directory / 'verifier-home'
    home.mkdir(mode=0o700)
    env = {'HOME': str(home), 'PATH': '/usr/bin:/bin'}
    binary = directory / 'cosign'
    checked_download('https://github.com/sigstore/cosign/releases/download/v' + lock['cosignVersion'] +
                     '/cosign-linux-amd64', lock['cosignSha256'], binary)
    binary.chmod(0o500)
    url = UPSTREAM + '/releases/download/v' + lock['version'] + '/caddy_' + lock['version'] + '_linux_amd64'
    archive = checked_download(url + '.tar.gz', lock['archiveSha256'], directory / 'caddy.tar.gz')
    checked_download(url + '.pem', lock['certificateSha256'], directory / 'caddy.pem')
    checked_download(url + '.tar.gz.sig', lock['signatureSha256'], directory / 'caddy.sig')
    run(upstream_command(binary, directory, lock), env=env)
    raw = run([str(binary), 'verify', BASE + '@' + lock['baseIndex'],
               '--certificate-identity', 'keyless@distroless.iam.gserviceaccount.com',
               '--certificate-oidc-issuer', 'https://accounts.google.com'], env=env)
    (directory / 'base-signature-verification.json').write_bytes(raw)
    blobs = {}
    def read(digest, kind):
        raw = read_public('https://gcr.io/v2/distroless/static-debian13/' + kind + '/' + digest, 1024 * 1024)
        require(blob_digest(raw) == digest, 'caddy-base-digest-mismatch')
        blobs[digest] = raw
        return json.loads(raw)
    index = read(lock['baseIndex'], 'manifests')
    require([m['digest'] for m in index['manifests'] if m.get('platform') ==
             {'os': 'linux', 'architecture': 'amd64'}] == [lock['baseManifest']], 'caddy-base-platform-mismatch')
    manifest = read(lock['baseManifest'], 'manifests')
    read(manifest['config']['digest'], 'blobs')
    map_oci_bytes(BASE + '@' + lock['baseIndex'], BASE, blobs)
    extract_binary(archive, lock, context)
    for name in ('Dockerfile', '.dockerignore'):
        shutil.copyfile(INPUTS / name, context / name)
    (directory / 'inputs.json').write_text(json.dumps(input_predicate(lock), sort_keys=True) + '\n')


def input_predicate(lock):
    return {'schema': 'portfolio.caddy-inputs/v1', 'platform': 'linux/amd64',
            'upstreamRepository': 'caddyserver/caddy', 'inputs': lock,
            'recipeSha256': hashlib.sha256((INPUTS / 'Dockerfile').read_bytes()).hexdigest(),
            'upstreamVerification': 'cosign-fulcio-rekor-sct',
            'baseSigner': 'keyless@distroless.iam.gserviceaccount.com'}


def published_inputs(lock, source_sha, run_id, run_attempt):
    require(isinstance(source_sha, str) and re.fullmatch(r'[0-9a-f]{40}', source_sha) is not None and
            all(isinstance(v, str) and re.fullmatch(r'[1-9][0-9]{0,19}', v) is not None
                for v in (run_id, run_attempt)), 'caddy-publication-identity-invalid')
    return {**input_predicate(lock), 'sourceRepository': REPOSITORY, 'sourceSha': source_sha,
            'sourceRef': 'refs/heads/main', 'runId': run_id, 'runAttempt': run_attempt}


def wait_for_offline(github_factory, sha, *, sleep=None):
    """Wait only for the exact push CI; all policy checks still run afterward."""
    import time
    from scripts.production_integration.release_transport import bounded_items
    sleep = time.sleep if sleep is None else sleep
    identity = None
    for poll in range(40):
        runs = bounded_items(github_factory().get(REPOSITORY,
            '/actions/runs?head_sha=' + sha + '&per_page=100'), 'workflow_runs')
        matches = [r for r in runs if r.get('path') == '.github/workflows/offline.yml' and
                   r.get('event') == 'push']
        require(len(matches) <= 1, 'caddy-offline-run-ambiguous')
        if matches:
            run = matches[0]
            require(run.get('head_sha') == sha and run.get('head_branch') == 'main' and
                    run.get('repository', {}).get('full_name') == REPOSITORY and
                    run.get('head_repository', {}).get('full_name') == REPOSITORY and
                    type(run.get('id')) is int and run['id'] > 0 and
                    type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
                    'caddy-offline-run-untrusted')
            current = (run['id'], run['run_attempt'])
            require(identity is None or identity == current, 'caddy-offline-run-changed')
            identity = current
            if run.get('status') == 'completed':
                require(run.get('conclusion') == 'success', 'caddy-offline-run-failed')
                return
            require(run.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested') and
                    run.get('conclusion') is None, 'caddy-offline-run-untrusted')
        if poll < 39:
            sleep(15)
    raise DockerError('caddy-offline-run-timeout')


def verify_main_source(github_factory, sha):
    """Shared full source verification for publication and read-only App smoke."""
    from scripts.production_integration.release_source import ProtectedSource, PRODUCTION_REVIEW_POLICY, WorkflowCheck
    github = github_factory()
    require(github.get(REPOSITORY, '/branches/main')['commit']['sha'] == sha,
            'caddy-main-head-changed')
    wait_for_offline(github_factory, sha)
    github = github_factory()
    checks = (WorkflowCheck(REPOSITORY, 'Offline checks', 15368, '.github/workflows/offline.yml'),)
    ProtectedSource(github, review_policy=PRODUCTION_REVIEW_POLICY).collect(REPOSITORY, sha, checks)
    # ProtectedSource rechecks policy/head before returning; retain the final
    # publication-specific exact-head check too.
    require(github.get(REPOSITORY, '/branches/main')['commit']['sha'] == sha,
            'caddy-main-head-changed')


def publication_guard():
    from scripts.production_integration.release_transport import GitHubRead, HTTPSRead
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and
            os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and
            os.environ.get('GITHUB_REF_PROTECTED') == 'true' and
            re.fullmatch(r'[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) is not None,
            'caddy-protected-main-required')
    github_factory = lambda: GitHubRead(os.environ.get('GH_TOKEN'), HTTPSRead())
    verify_main_source(github_factory, os.environ['GITHUB_SHA'])


# Exact allowlist: never print an exception, arbitrary remote text, a URL or
# credential. Unknown failures get a fixed code even if their message looks safe.
SOURCE_FAILURE_CODES = frozenset({
    'caddy-protected-main-required', 'caddy-protected-develop-required', 'caddy-main-head-changed',
    'caddy-offline-run-ambiguous', 'caddy-offline-run-untrusted', 'caddy-offline-run-changed',
    'caddy-offline-run-failed', 'caddy-offline-run-timeout',
    'production-release-token-unavailable', 'production-release-host-rejected',
    'production-release-api-path-rejected', 'production-release-budget-exhausted',
    'production-release-api-incomplete', 'production-release-api-timeout',
    'production-release-response-too-large', 'production-release-api-pagination-unproven',
    'production-release-api-authentication-failed', 'production-release-api-denied-or-rate-limited',
    'production-release-api-not-found', 'production-release-api-unavailable',
    'production-release-api-timeout-or-unavailable', 'production-release-sha-not-on-main',
    'production-release-source-changed',
    'production-release-protection-insufficient', 'production-release-protection-unverifiable',
    'production-release-protection-bypass-unreadable', 'production-release-protection-app-unbound',
    'production-release-required-check-policy-mismatch', 'production-release-required-check-unavailable',
    'production-release-required-check-untrusted', 'production-release-check-workflow-mismatch',
    'production-release-workflow-policy-invalid', 'production-release-review-policy-invalid',
})


def source_failure_code(error):
    if isinstance(error, DockerError) and str(error) in SOURCE_FAILURE_CODES:
        return str(error)
    if isinstance(error, (KeyError, TypeError, ValueError, AttributeError)):
        return 'caddy-source-response-incomplete'
    return 'caddy-source-verification-failed'


TOOLCHAIN_FAILURE_CODES = frozenset(
    'caddy-builder-' + component + '-' + reason
    for component in ('runner', 'docker', 'buildx', 'compose')
    for reason in ('drift', 'unavailable')
)


def toolchain_value(value, *, runner=False):
    # Only bounded numeric versions can enter logs; never echo process output,
    # environment contents, Docker metadata, exception text or stderr.
    pattern = r'[0-9]{1,12}\.[0-9]{1,12}\.[0-9]{1,12}' if runner else r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else 'unavailable'


def check_toolchain():
    lock = load_lock()
    runner = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', RUNNER_ARCH='X64', RUNNER_OS='Linux',
                  ImageVersion=lock['runnerImageVersion'])
    for key, expected in runner.items():
        actual = os.environ.get(key)
        # For non-version fields report only the match, not untrusted env values.
        record = dict(check='runner.' + key, status='ok' if actual == expected else 'drift')
        if key == 'ImageVersion':
            record.update(expected=expected, actual=toolchain_value(actual, runner=True))
        print(json.dumps(record, sort_keys=True))
    require(all(os.environ.get(key) == value for key, value in runner.items()), 'caddy-builder-runner-drift')
    failures = []
    for component, command in (
        ('docker', ['docker', 'version', '--format', '{{json .}}']),
        ('buildx', ['docker', 'buildx', 'version']),
        ('compose', ['docker', 'compose', 'version', '--short']),
    ):
        try:
            raw = run(command)
            if component == 'docker':
                versions = json.loads(raw)
                actual = {side.lower(): versions[side]['Version'] for side in ('Client', 'Server')}
            elif component == 'buildx':
                parts = raw.decode().split()
                actual = {'version': parts[1].removeprefix('v')}
                require(parts[1].startswith('v'), 'caddy-builder-buildx-unavailable')
            else:
                actual = {'version': raw.decode().strip().removeprefix('v')}
            expected = lock[component + 'Version']
            matches = all(value == expected for value in actual.values())
            safe = {key: toolchain_value(value) for key, value in actual.items()}
            print(json.dumps(dict(check=component, expected=expected, actual=safe,
                                  status='ok' if matches else 'drift'), sort_keys=True))
            if not matches:
                failures.append('caddy-builder-' + component + '-drift')
        except Exception:
            # A missing command, timeout or malformed response remains a failure.
            print(json.dumps(dict(check=component, status='unavailable'), sort_keys=True))
            failures.append('caddy-builder-' + component + '-unavailable')
    if failures:
        raise DockerError(failures[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'check', 'publish-guard', 'toolchain-check', 'publication-inputs'))
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'prepare':
            require(args.directory is not None and args.directory.is_absolute(), 'caddy-directory-required')
            prepare(args.directory)
        elif args.operation == 'publish-guard':
            publication_guard()
        elif args.operation == 'toolchain-check':
            check_toolchain()
        elif args.operation == 'publication-inputs':
            lock = load_lock()
            require(json.loads((args.directory / 'inputs.json').read_bytes()) == input_predicate(lock),
                    'caddy-build-input-evidence-changed')
            evidence = published_inputs(lock, os.environ['GITHUB_SHA'], os.environ['GITHUB_RUN_ID'],
                                        os.environ['GITHUB_RUN_ATTEMPT'])
            (args.directory / 'publication-inputs.json').write_text(json.dumps(evidence, sort_keys=True) + '\n')
        else:
            load_lock()
    except Exception as error:
        # CLI boundary: unexpected dependency failures must not print secrets in
        # a traceback either. Interrupts still propagate normally.
        code = source_failure_code(error) if args.operation == 'publish-guard' else 'caddy-producer-inputs-failed'
        if args.operation == 'toolchain-check' and isinstance(error, DockerError) and str(error) in TOOLCHAIN_FAILURE_CODES:
            code = str(error)
        print(code)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
