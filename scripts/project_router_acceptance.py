"""Dispatch-only disposable GitHub runner acceptance; never production admission.

No producer checkout/build, production adapter, gateway or deployment lifecycle.
The Compose renderer's test-session mode provides isolated resource identities.
"""
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from scripts.compose import LABEL, render_compose
from scripts.docker_discovery import DockerResource, container_runtime
from scripts.planning_inputs import build_desired_state, parse_snapshot
from scripts.production_integration.artifacts import (
    INDEX_TYPES, bind_engine_image, blob_digest, verify_oci_bytes,
)
from scripts.reconciliation import encode

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'ghcr.io/danilfaritovich/project-router'
DIGEST = 'sha256:a393d592331cd556650695f57db74075359d9223c9ebbda080f1a372e9d64853'
IMAGE = REPOSITORY + '@' + DIGEST
SOURCE = '526c765f33932e163392dd1d512de4aa2f2a5260'
PRODUCER = 'DanilFaritovich/project-router'
ORIGIN = 'https://danil-miftakhov.dev'
ORIGIN_TYPE = 'https://github.com/' + PRODUCER + '/attestations/build-origin/v1'
SLSA_TYPE = 'https://slsa.dev/provenance/v1'
LIMIT = 1024 * 1024


class AcceptanceError(Exception):
    """Only fixed stage identifiers are exposed in the public failure report."""


def require(condition):
    if not condition:
        raise AcceptanceError()


def runner_gate(env):
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    require(env.get('RUNNER_OS') == 'Linux' and env.get('RUNNER_ARCH') == 'X64')
    require(env.get('GITHUB_REPOSITORY') == 'DanilFaritovich/portfolio-deployment')
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch')
    require(env.get('GITHUB_REF') in ('refs/heads/main', 'refs/heads/develop'))
    require(env.get('ACCEPTANCE_REF_PROTECTED') == 'true')
    require(env.get('ACCEPTANCE_CONFIRM') == 'project-router-v1.0.0-isolated')
    require(re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', '')) is not None)
    require(env.get('DOCKER_HOST', '') in ('', 'unix:///var/run/docker.sock'))


def command(args, *, timeout=60, limit=4 * LIMIT, gh=False):
    # Do not propagate Actions/production credentials into Docker or containers.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}
    env.update({'DOCKER_HOST': 'unix:///var/run/docker.sock', 'DOCKER_CONFIG': os.environ['ACCEPTANCE_DOCKER_CONFIG']})
    if gh:
        env['GH_TOKEN'] = os.environ['GH_TOKEN']
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(args, env=env, stdout=output, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        require(result.returncode == 0 and output.tell() <= limit)
        output.seek(0)
        return output.read(limit + 1)


def decoded(raw):
    return parse_snapshot(raw)


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # GHCR may redirect config blobs; never forward its bearer token to CDN.
        url = urllib.parse.urlsplit(newurl)
        require(url.scheme == 'https' and url.hostname in ('ghcr.io', 'pkg-containers.githubusercontent.com'))
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


def fetch(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RegistryRedirect())
    with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=20) as response:
        raw = response.read(LIMIT + 1)
    require(len(raw) <= LIMIT)
    return raw


def registry_blobs():
    token = decoded(fetch('https://ghcr.io/token?service=ghcr.io&scope=repository:danilfaritovich/project-router:pull'))['token']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': ', '.join(sorted(INDEX_TYPES | {
        'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}))}
    blobs = {}
    def read(digest, kind):
        require(isinstance(digest, str) and re.fullmatch('sha256:[0-9a-f]{64}', digest) is not None)
        raw = fetch('https://ghcr.io/v2/danilfaritovich/project-router/' + kind + '/' + digest, headers)
        require(blob_digest(raw) == digest)
        blobs[digest] = raw
        return decoded(raw)
    manifest = read(DIGEST, 'manifests')
    if manifest.get('mediaType') in INDEX_TYPES:
        descriptors = manifest.get('manifests', [])
        require(isinstance(descriptors, list) and len(descriptors) <= 64)
        matches = [d for d in descriptors if d.get('platform') == {'os': 'linux', 'architecture': 'amd64'}]
        require(len(matches) == 1)
        manifest = read(matches[0]['digest'], 'manifests')
    read(manifest['config']['digest'], 'blobs')
    # All sizes/media/platform and duplicate JSON keys checked by shared verifier.
    return blobs, verify_oci_bytes(IMAGE, REPOSITORY, blobs)


def attestation_command(predicate):
    return ['gh', 'attestation', 'verify', 'oci://' + IMAGE, '--repo', PRODUCER,
            '--signer-workflow', 'github.com/' + PRODUCER + '/.github/workflows/publish.yml',
            '--signer-digest', SOURCE, '--source-digest', SOURCE, '--source-ref', 'refs/heads/main',
            '--deny-self-hosted-runners', '--predicate-type', predicate, '--format', 'json', '--limit', '10']


def verified_statement(raw, predicate):
    """Input must be stdout of successful gh verification, never a supplied file."""
    results = decoded(raw)
    require(isinstance(results, list) and 0 < len(results) <= 10)
    statements = [r['verificationResult']['statement'] for r in results]
    matches = [s for s in statements if s.get('predicateType') == predicate and any(
        sub.get('name') == REPOSITORY and sub.get('digest') == {'sha256': DIGEST[7:]}
        for sub in s.get('subject', []))]
    require(len(matches) == 1)
    return matches[0]


def verify_origin(statement):
    p = statement['predicate']
    require(p == {'sourceRepository': PRODUCER, 'sourceSha': SOURCE, 'sourceRef': 'refs/heads/main',
                  'platform': 'linux/amd64', 'buildArguments': {'VITE_SITE_ORIGIN': ORIGIN},
                  'originOutputVerified': True, 'runId': '39776962924', 'runAttempt': '1'})


def candidate_model(directory, revision, session):
    """Validate the real candidate with unchanged policy in a temporary registry."""
    (directory / 'registry/applications').mkdir(parents=True)
    (directory / 'policy').mkdir()
    shutil.copyfile(ROOT / 'policy/admission.yaml', directory / 'policy/admission.yaml')
    shutil.copyfile(ROOT / 'docs/candidates/project-router.yaml', directory / 'registry/applications/project-router.yaml')
    desired = build_desired_state(directory, revision)
    require(len(desired.applications) == 1)
    app = desired.applications[0]
    require(app.id == 'project-router' and app.spec['enabled'] is False)
    require(app.spec['components'][0]['image'] == IMAGE)
    # Also pin the entire runtime/security contract to the reviewed template.
    from scripts.validate_registry import load_document
    expected = load_document((ROOT / 'docs/templates/project-router.yaml').read_bytes().replace(
        b'${PROJECT_ROUTER_DIGEST}', DIGEST.encode()))
    actual = load_document((directory / 'registry/applications/project-router.yaml').read_bytes())
    require(actual == expected)
    return render_compose(app, test_session=session)


def runtime_matches(document, artifact, evidence):
    f = {'config': document['Config'], 'host': document['HostConfig'], 'imageId': document['Image'],
         'networks': document['NetworkSettings']['Networks'], 'mounts': document['Mounts']}
    resource = DockerResource('container', document['Id'], document['Name'].lstrip('/'), encode(f))
    return container_runtime(resource, artifact, 'web', evidence)


def request(ip, path):
    require(ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback)
    require(path.startswith('/') and not path.startswith('//') and len(path) <= 512)
    conn = http.client.HTTPConnection(ip, 8080, timeout=5)
    try:
        conn.request('GET', path, headers={'Host': 'danil-miftakhov.dev'})
        response = conn.getresponse()
        body = response.read(16 * LIMIT + 1)
        require(len(body) <= 16 * LIMIT)
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def http_checks(ip):
    records = []
    def check(path, status, mime=None, prefix=None):
        code, headers, body = request(ip, path)
        require(code == status)
        if mime:
            require(headers.get('Content-Type', '').split(';')[0] == mime)
        if prefix:
            require(body.startswith(prefix))
        records.append({'path': path, 'status': code, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
        return headers, body
    for locale in ('en', 'ru'):
        _, body = check('/' + locale + '/', 200, 'text/html')
        require(('lang="' + locale + '"').encode() in body)
        require((ORIGIN + '/' + locale + '/').encode() in body)
        # Representative generated JS and CSS paths, served from this same image.
        for extension, mime in (('js', 'application/javascript'), ('css', 'text/css')):
            paths = re.findall(rb'(?:src|href)="(/assets/[^"?#]+\.' + extension.encode() + rb')"', body)
            require(bool(paths))
            check(paths[0].decode(), 200, mime)
    headers, _ = check('/', 302)
    require(headers.get('Location') in ('/en/', ORIGIN + '/en/'))
    _, body = check('/manager-acceptance-nonexistent', 404)
    require(b'<html' in body.lower())
    for path in ('/resumes/backend-en.pdf', '/resumes/backend-ru.pdf'):
        check(path, 200, 'application/pdf', b'%PDF-')
    check('/social/portfolio-en.png', 200, 'image/png', b'\x89PNG\r\n\x1a\n')
    for path in ('/sitemap.xml', '/robots.txt'):
        _, body = check(path, 200)
        require(ORIGIN.encode() in body)
    return records


class DisposableRuntime:
    def __init__(self, artifact, directory, session):
        self.artifact, self.session = artifact, session
        self.file = directory / 'compose.json'
        self.file.write_text(artifact.to_json())
        self.proxy_id = None
        self.active = False

    def docker(self, *args, **kwargs):
        return command(['docker', *args], **kwargs)

    def compose(self, *args):
        return self.docker('compose', '--project-name', self.artifact.project, '--file', str(self.file), *args, timeout=120)

    def start(self):
        names = {n['name'] for n in self.artifact.model['networks'].values()}
        existing = self.docker('network', 'ls', '--format', '{{.Name}}').decode().splitlines()
        require(not names.intersection(existing))
        require(not self.containers())
        proxy = self.artifact.model['networks']['proxy']['name']
        self.proxy_id = self.docker('network', 'create', '--internal', '--label', LABEL + '.test-session=' + self.session,
                                    proxy).decode().strip()
        self.compose('config', '--quiet')
        self.active = True
        self.compose('up', '--detach', '--no-build', '--pull', 'never', 'web')

    def containers(self):
        return self.docker('ps', '-aq', '--filter', 'label=' + LABEL + '.test-session=' + self.session,
                           '--filter', 'label=com.docker.compose.project=' + self.artifact.project).decode().split()

    def inspect(self):
        ids = self.containers()
        require(len(ids) == 1)
        return decoded(self.docker('container', 'inspect', ids[0]))[0]

    def healthy(self, evidence):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            doc = self.inspect()
            require(runtime_matches(doc, self.artifact, evidence))
            require(doc['State']['Running'])
            status = doc['State'].get('Health', {}).get('Status')
            require(status in ('starting', 'healthy'))
            if status == 'healthy':
                return doc
            time.sleep(2)
        raise AcceptanceError()

    def security(self, doc):
        # Verify effective process credentials/caps, not merely Docker config.
        raw = self.docker('exec', doc['Id'], 'cat', '/proc/1/status').decode()
        fields = dict(line.split(':', 1) for line in raw.splitlines() if ':' in line)
        require(fields['Uid'].split() == ['101'] * 4 and fields['Gid'].split() == ['101'] * 4)
        require(all(int(fields[key].strip(), 16) == 0 for key in ('CapEff', 'CapPrm', 'CapBnd', 'CapAmb')))
        require(fields['NoNewPrivs'].strip() == '1')
        mounts = self.docker('exec', doc['Id'], 'cat', '/proc/1/mounts').decode().splitlines()
        tmp = [line.split()[3].split(',') for line in mounts if line.split()[1] == '/tmp']
        require(len(tmp) == 1 and {'rw', 'noexec', 'nosuid', 'nodev', 'size=16384k'}.issubset(tmp[0]))
        ports = doc['NetworkSettings'].get('Ports') or {}
        require(all(not bindings for bindings in ports.values()))
        # Only /tmp is writable; startup/request writes must not alter image root.
        require(not self.docker('diff', doc['Id']).strip())

    def cleanup(self):
        # No down/prune, image removal or volumes. Exact session + project labels.
        if self.active:
            for identifier in self.containers():
                doc = decoded(self.docker('container', 'inspect', identifier))[0]
                labels = doc['Config']['Labels']
                require(labels.get(LABEL + '.test-session') == self.session)
                require(labels.get('com.docker.compose.project') == self.artifact.project)
                self.docker('container', 'rm', '--force', identifier)
            ids = self.docker('network', 'ls', '-q', '--filter', 'label=' + LABEL + '.test-session=' + self.session).decode().split()
            for identifier in ids:
                doc = decoded(self.docker('network', 'inspect', identifier))[0]
                require(doc['Name'] in {n['name'] for n in self.artifact.model['networks'].values()})
                require(doc['Labels'].get(LABEL + '.test-session') == self.session and not doc.get('Containers'))
                self.docker('network', 'rm', identifier)
        elif self.proxy_id:
            doc = decoded(self.docker('network', 'inspect', self.proxy_id))[0]
            require(doc['Labels'].get(LABEL + '.test-session') == self.session and not doc.get('Containers'))
            self.docker('network', 'rm', self.proxy_id)


def main():
    report = {'schema': 'portfolio.isolated-acceptance/v1', 'result': 'failed', 'productionAdmission': False,
              'image': IMAGE, 'producerSha': SOURCE, 'producerRun': '39776962924/1', 'stage': 'runner-gate'}
    output = ROOT / 'acceptance-evidence'
    runtime = None
    report_ready = False
    try:
        runner_gate(os.environ)
        output.mkdir(exist_ok=False)
        report_ready = True
        with tempfile.TemporaryDirectory(prefix='pd-acceptance-') as temporary:
            directory = Path(temporary)
            config = directory / 'docker-config'
            config.mkdir(mode=0o700)
            os.environ['ACCEPTANCE_DOCKER_CONFIG'] = str(config)
            report['managerSha'] = os.environ['GITHUB_SHA']
            report['stage'] = 'candidate'
            session = secrets.token_hex(8)
            artifact = candidate_model(directory / 'registry-root', report['managerSha'], session)
            report['composeSha256'] = artifact.sha256
            report['stage'] = 'registry-bytes'
            blobs, evidence = registry_blobs()
            report['mapping'] = {'manifest': evidence.manifest_digest, 'config': evidence.image_id,
                                 'blobs': {d: len(raw) for d, raw in sorted(blobs.items())}}
            report['stage'] = 'signed-attestations'
            report['attestations'] = {}
            for predicate in (SLSA_TYPE, ORIGIN_TYPE):
                raw = command(attestation_command(predicate), timeout=180, gh=True)
                statement = verified_statement(raw, predicate)
                if predicate == ORIGIN_TYPE:
                    verify_origin(statement)
                report['attestations'][predicate] = {'verifiedOutputSha256': hashlib.sha256(raw).hexdigest(),
                                                     'sourceSha': SOURCE, 'sourceRef': 'refs/heads/main'}
            report['stage'] = 'pull-engine-binding'
            command(['docker', 'pull', '--quiet', '--platform', 'linux/amd64', IMAGE], timeout=180)
            inspected = decoded(command(['docker', 'image', 'inspect', IMAGE]))[0]
            bind_engine_image(evidence, inspected)
            require(inspected['Config'].get('Healthcheck', {}).get('Test', [None])[0] not in (None, 'NONE'))
            report['stage'] = 'runtime-start'
            runtime = DisposableRuntime(artifact, directory, session)
            try:
                runtime.start()
                doc = runtime.healthy(evidence)
                report['stage'] = 'http-security'
                private = artifact.model['networks']['private']['name']
                ip = doc['NetworkSettings']['Networks'][private]['IPAddress']
                report['http'] = http_checks(ip)
                runtime.security(runtime.inspect())
                report['stage'] = 'restart'
                started = doc['State']['StartedAt']
                runtime.compose('restart', 'web')
                doc = runtime.healthy(evidence)
                require(doc['State']['StartedAt'] != started)
                ip = doc['NetworkSettings']['Networks'][private]['IPAddress']
                require(request(ip, '/en/')[0] == 200 and request(ip, '/ru/')[0] == 200)
                runtime.security(doc)
                report['runtimeSecurity'] = 'passed'
            finally:
                report['cleanup'] = 'failed'
                runtime.cleanup()
                report['cleanup'] = 'passed'
            report.update(result='passed', stage='complete')
    except Exception:
        # No exception text, raw command output, env, inspect or container logs.
        pass
    finally:
        if report_ready:
            (output / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print('Isolated acceptance: ' + report['result'] + ' (' + report['stage'] + '); production admission: false')
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
