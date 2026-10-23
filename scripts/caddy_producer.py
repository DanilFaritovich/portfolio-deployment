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
BUILDER = 'https://github.com/actions/runner/github-hosted'
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


def publication_guard():
    from scripts.production_integration.release_source import ProtectedSource, PRODUCTION_REVIEW_POLICY, WorkflowCheck
    from scripts.production_integration.release_transport import GitHubRead, HTTPSRead
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and
            os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and
            os.environ.get('GITHUB_REF_PROTECTED') == 'true' and
            re.fullmatch(r'[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) is not None,
            'caddy-protected-main-required')
    github_factory = lambda: GitHubRead(os.environ.get('GH_TOKEN'), HTTPSRead())
    github = github_factory()
    require(github.get(REPOSITORY, '/branches/main')['commit']['sha'] == os.environ['GITHUB_SHA'],
            'caddy-main-head-changed')
    wait_for_offline(github_factory, os.environ['GITHUB_SHA'])
    github = github_factory()
    ProtectedSource(github, review_policy=PRODUCTION_REVIEW_POLICY).collect(REPOSITORY, os.environ['GITHUB_SHA'],
        (WorkflowCheck(REPOSITORY, 'Offline checks', 15368, '.github/workflows/offline.yml'),))
    require(github.get(REPOSITORY, '/branches/main')['commit']['sha'] == os.environ['GITHUB_SHA'],
            'caddy-main-head-changed')


def check_toolchain():
    lock = load_lock()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
            os.environ.get('RUNNER_ARCH') == 'X64' and os.environ.get('RUNNER_OS') == 'Linux' and
            os.environ.get('ImageVersion') == lock['runnerImageVersion'], 'caddy-builder-runner-drift')
    versions = json.loads(run(['docker', 'version', '--format', '{{json .}}']))
    require(versions['Client']['Version'] == versions['Server']['Version'] == lock['dockerVersion'],
            'caddy-builder-docker-drift')
    buildx = run(['docker', 'buildx', 'version']).decode().split()
    require(len(buildx) >= 2 and buildx[1] == 'v' + lock['buildxVersion'], 'caddy-builder-buildx-drift')
    compose = run(['docker', 'compose', 'version', '--short']).decode().strip().removeprefix('v')
    require(compose == lock['composeVersion'], 'caddy-builder-compose-drift')


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
    except (DockerError, OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError):
        print('caddy-producer-inputs-failed')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
