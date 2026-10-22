"""Dispatch-only disposable GitHub runner acceptance; never production admission.

No producer checkout/build, production adapter, gateway or deployment lifecycle.
The Compose renderer's test-session mode provides isolated resource identities.
"""
import hashlib
from contextlib import contextmanager
import http.client
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

from scripts.compose import LABEL, startup_order
from scripts.acceptance_contract import AcceptanceError, AcceptanceBlocked, require, candidate_model
from scripts.docker_discovery import DockerResource, container_runtime
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.artifacts import (
    INDEX_TYPES, EngineBindingError, bind_engine_image, blob_digest, verify_oci_bytes,
)
from scripts.reconciliation import encode
from scripts.readiness import ProbeResult, ReadinessPolicy, ReadinessContractError, verify_readiness

ROOT = Path(__file__).resolve().parents[1]
SLSA_TYPE = 'https://slsa.dev/provenance/v1'
LIMIT = 1024 * 1024
# Official cli/cli v2.102.0 release checksums; never trust runner PATH or a
# checksum downloaded beside the archive at runtime. GHSA-wjmr-j3rp-mh2g.
GH_VERSION = '2.102.0'
GH_MINIMUM = (2, 102, 0)
GH_ARCHIVE = 'gh_' + GH_VERSION + '_linux_amd64.tar.gz'
GH_ARCHIVE_SHA256 = 'bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386'


def runner_gate(env):
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    require(env.get('RUNNER_OS') == 'Linux' and env.get('RUNNER_ARCH') == 'X64')
    require(env.get('GITHUB_REPOSITORY') == 'DanilFaritovich/portfolio-deployment')
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch')
    require(env.get('GITHUB_REF') in ('refs/heads/main', 'refs/heads/develop'))
    require(env.get('ACCEPTANCE_REF_PROTECTED') == 'true')
    require(env.get('ACCEPTANCE_CONFIRM') == 'isolated-acceptance')
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


def registry_blobs(image):
    repository, digest = image.split('@')
    path = repository.removeprefix('ghcr.io/')
    token = decoded(fetch('https://ghcr.io/token?service=ghcr.io&scope=repository:' + path + ':pull'))['token']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': ', '.join(sorted(INDEX_TYPES | {
        'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}))}
    blobs = {}
    def read(digest, kind):
        require(isinstance(digest, str) and re.fullmatch('sha256:[0-9a-f]{64}', digest) is not None)
        raw = fetch('https://ghcr.io/v2/' + path + '/' + kind + '/' + digest, headers)
        require(blob_digest(raw) == digest)
        blobs[digest] = raw
        return decoded(raw)
    manifest = read(digest, 'manifests')
    if manifest.get('mediaType') in INDEX_TYPES:
        descriptors = manifest.get('manifests', [])
        require(isinstance(descriptors, list) and len(descriptors) <= 64)
        matches = [d for d in descriptors if d.get('platform') == {'os': 'linux', 'architecture': 'amd64'}]
        require(len(matches) == 1)
        manifest = read(matches[0]['digest'], 'manifests')
    read(manifest['config']['digest'], 'blobs')
    # All sizes/media/platform and duplicate JSON keys checked by shared verifier.
    return blobs, verify_oci_bytes(image, repository, blobs)


def verify_gh_version(raw):
    lines = raw.decode('ascii').splitlines()
    require(bool(lines))
    match = re.fullmatch(r'gh version ([0-9]+\.[0-9]+\.[0-9]+) \([0-9]{4}-[0-9]{2}-[0-9]{2}\)', lines[0])
    require(match is not None)
    version = match.group(1)
    require(tuple(map(int, version.split('.'))) >= GH_MINIMUM and version == GH_VERSION)


def provision_gh(directory):
    """Authenticate archive before parsing/executing; no installed-gh fallback."""
    url = 'https://github.com/cli/cli/releases/download/v' + GH_VERSION + '/' + GH_ARCHIVE
    raw = command(['curl', '--fail', '--silent', '--show-error', '--location',
                   '--proto', '=https', '--proto-redir', '=https', '--max-time', '120',
                   '--max-filesize', str(32 * LIMIT), url], timeout=130, limit=32 * LIMIT)
    require(hashlib.sha256(raw).hexdigest() == GH_ARCHIVE_SHA256)
    # Read only the exact regular-file member: never extract paths, links or
    # archive-supplied permissions. The enclosing temporary directory is private.
    member_name = GH_ARCHIVE.removesuffix('.tar.gz') + '/bin/gh'
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.name == member_name]
        require(len(members) == 1 and members[0].isfile() and 0 < members[0].size <= 128 * LIMIT)
        with archive.extractfile(members[0]) as source:
            binary = source.read(128 * LIMIT + 1)
        require(len(binary) == members[0].size)
    path = directory / 'gh'
    with path.open('xb') as target:
        target.write(binary)
    path.chmod(0o500)
    verify_gh_version(command([str(path), '--version'], limit=4096))
    return str(path)


def attestation_command(image, producer, identity, predicate, gh_binary):
    require(Path(gh_binary).is_absolute())
    return [gh_binary, 'attestation', 'verify', 'oci://' + image, '--repo', producer,
            '--signer-workflow', 'github.com/' + producer + '/' + identity['signerWorkflow'],
            '--signer-digest', identity['signerSha'], '--source-digest', identity['sourceSha'],
            '--source-ref', identity['sourceRef'], '--deny-self-hosted-runners',
            '--predicate-type', predicate, '--format', 'json', '--limit', '10']


def verified_statement(raw, image, expectation):
    """Only successful pinned gh verification stdout may reach this function."""
    repository, digest = image.split('@')
    results = decoded(raw)
    require(isinstance(results, list) and 0 < len(results) <= 10)
    statements = [r['verificationResult']['statement'] for r in results]
    matches = [s for s in statements if s.get('predicateType') == expectation['predicateType'] and any(
        sub.get('name') == repository and sub.get('digest') == {'sha256': digest[7:]}
        for sub in s.get('subject', []))]
    require(len(matches) == 1)
    if 'expectedPredicate' in expectation:
        require(encode(matches[0].get('predicate')) == encode(expectation['expectedPredicate']))
    return matches[0]


def runtime_matches(document, artifact, service, evidence):
    facts = {'config': document['Config'], 'host': document['HostConfig'], 'imageId': document['Image'],
             'networks': document['NetworkSettings']['Networks'], 'mounts': document['Mounts']}
    resource = DockerResource('container', document['Id'], document['Name'].lstrip('/'), encode(facts))
    return container_runtime(resource, artifact, service, evidence)


def request(ip, port, path, hostname, timeout=5):
    address = ipaddress.ip_address(ip)
    require(address.version == 4 and address.is_private and not address.is_loopback and not address.is_unspecified)
    require(path.startswith('/') and not path.startswith('//') and len(path) <= 256)
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        conn.request('GET', path, headers={'Host': hostname})
        response = conn.getresponse()
        require(len(response.read(65537)) <= 65536)
        return response.status == 200
    finally:
        conn.close()


# Fixed host-side probe enters ONLY the source container's network namespace.
# No producer code/shell/tooling, mounts or Docker socket inside any container.
NETWORK_PROBE = r'''
import socket, struct, sys
name, expected, port = sys.argv[1:]
query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
query += b''.join(bytes([len(p)]) + p.encode('ascii') for p in name.split('.')) + b'\x00\x00\x01\x00\x01'
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(5)
s.sendto(query, ('127.0.0.11', 53))
data = s.recv(4096)
assert data[:2] == b'\x12\x34' and data[3] & 15 == 0 and data[2] & 2 == 0
questions, answers = struct.unpack('!HH', data[4:8])
assert questions == 1 and 0 < answers <= 32
def skip(offset):
    for _ in range(128):
        n = data[offset]
        if n & 192 == 192:
            return offset + 2
        offset += 1
        if n == 0:
            return offset
        assert n <= 63
        offset += n
    raise ValueError()
offset = skip(12) + 4
ips = []
for _ in range(answers):
    offset = skip(offset)
    kind, cls, ttl, size = struct.unpack('!HHIH', data[offset:offset+10])
    offset += 10
    if kind == 1 and cls == 1 and size == 4:
        ips.append(socket.inet_ntoa(data[offset:offset+size]))
    offset += size
assert ips == [expected]
if int(port):
    with socket.create_connection((expected, int(port)), timeout=5):
        pass
'''


class RuntimeFailure(AcceptanceError, ReadinessContractError):
    """Already classified locally; contains no transport or application data."""


class DisposableRuntime:
    def __init__(self, application, artifact, directory, session):
        self.application, self.artifact, self.session = application, artifact, session
        self.file = directory / 'compose.json'
        self.file.write_text(artifact.to_json())
        self.active = False
        self.components = {c['name']: c for c in application.spec['components']}
        self.diagnostics = {'steps': {}, 'probes': 0, 'consecutive': 0}

    @contextmanager
    def phase(self, step):
        # Call sites supply literals only. Never copy exceptions/command output.
        self.diagnostics['step'] = step
        self.diagnostics['steps'][step] = 'running'
        try:
            yield
        except RuntimeFailure:
            self.diagnostics['steps'][step] = 'failed'
            raise
        except Exception as error:
            self.fail(step + ('-timeout' if isinstance(error, TimeoutError) or
                             isinstance(error, subprocess.TimeoutExpired) else '-failed'))
        else:
            self.diagnostics['steps'][step] = 'passed'

    def fail(self, code):
        self.diagnostics['errorCode'] = code
        self.diagnostics['steps'][self.diagnostics['step']] = 'failed'
        raise RuntimeFailure() from None

    def docker(self, *args, **kwargs):
        return command(['docker', *args], **kwargs)

    def compose(self, *args):
        return self.docker('compose', '--project-name', self.artifact.project, '--file', str(self.file), *args, timeout=120)

    def owned(self, kind, service=None, timeout=60):
        args = ('ps', '-aq') if kind == 'container' else (kind, 'ls', '-q')
        filters = ('--filter', 'label=com.docker.compose.service=' + service) if service else ()
        return self.docker(*args, '--filter', 'label=' + LABEL + '.test-session=' + self.session,
                           '--filter', 'label=com.docker.compose.project=' + self.artifact.project,
                           *filters, timeout=timeout).decode().split()

    def ownership(self, doc, kind):
        labels = doc['Config']['Labels'] if kind == 'container' else doc['Labels']
        require(labels.get(LABEL + '.test-session') == self.session)
        require(labels.get('com.docker.compose.project') == self.artifact.project)
        require(labels.get(LABEL + '.application') == self.application.id)
        require(labels.get(LABEL + '.managed') == 'true')
        if kind == 'container':
            service = labels.get('com.docker.compose.service')
            require(service in self.components and labels.get(LABEL + '.component') == service)
        else:
            require(doc['Name'] in {n['name'] for n in self.artifact.model.get(kind + 's', {}).values()})

    def inspect(self, service, timeout=5):
        with self.phase('container-discovery'):
            identifiers = self.owned('container', service, timeout=timeout / 2)
            require(len(identifiers) == 1)
        with self.phase('container-inspect'):
            documents = decoded(self.docker('container', 'inspect', identifiers[0], timeout=timeout / 2))
            require(isinstance(documents, list) and len(documents) == 1 and isinstance(documents[0], dict))
            doc = documents[0]
        with self.phase('ownership'):
            self.ownership(doc, 'container')
            require(doc['Config']['Labels']['com.docker.compose.service'] == service)
        return doc

    def preflight(self):
        for kind in ('network', 'volume'):
            names = {n['name'] for n in self.artifact.model.get(kind + 's', {}).values()}
            existing = self.docker(kind, 'ls', '--format', '{{.Name}}').decode().splitlines()
            require(not names.intersection(existing))
        require(not self.docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + self.artifact.project).strip())
        with self.phase('compose-preflight'):
            self.compose('config', '--quiet')

    def start(self, evidence):
        with self.phase('resource-preflight'):
            self.preflight()
        # Mark before the first mutation: even partial command failures clean up.
        self.active = True
        proxy = self.artifact.model['networks'].get('proxy')
        if proxy:
            with self.phase('network-create'):
                self.docker('network', 'create', '--internal',
                        '--label', LABEL + '.test-session=' + self.session,
                        '--label', LABEL + '.application=' + self.application.id,
                        '--label', LABEL + '.managed=true',
                        '--label', 'com.docker.compose.project=' + self.artifact.project, proxy['name'])
        for name in startup_order(self.application):
            self.diagnostics['componentIndex'] = list(self.components).index(name)
            with self.phase('compose-up'):
                self.compose('up', '--detach', '--no-build', '--pull', 'never', '--no-deps', name)
            self.healthy(name, evidence[name])

    def ip(self, doc):
        network = self.artifact.model['networks']['private']['name']
        ip = doc['NetworkSettings']['Networks'][network]['IPAddress']
        address = ipaddress.ip_address(ip)
        require(address.version == 4 and address.is_private and not address.is_loopback and not address.is_unspecified)
        return ip

    def healthy(self, name, evidence):
        component = self.components[name]
        self.diagnostics['componentIndex'] = list(self.components).index(name)
        self.diagnostics.update(probes=0, consecutive=0, containerState='unknown',
                                dockerHealth='unknown', httpReadiness='not-probed',
                                exitCode='unknown', oomKilled='unknown')
        initial = self.inspect(name)
        with self.phase('container-identity'):
            container_id = initial['Id']
            require(isinstance(container_id, str) and bool(container_id))
        runtime = self
        class Probe:
            document = initial
            def probe(self, component, container_id, *, timeout, follow_redirects):
                runtime.diagnostics['probes'] += 1
                require(follow_redirects is False)
                doc = runtime.inspect(name, timeout=timeout / 2)
                with runtime.phase('container-identity'):
                    require(doc['Id'] == container_id)
                with runtime.phase('runtime-contract'):
                    require(runtime_matches(doc, runtime.artifact, name, evidence))
                self.document = doc
                with runtime.phase('container-state'):
                    state = doc['State']
                    running = state['Running']
                    require(type(running) is bool)
                    status = state.get('Status')
                    runtime.diagnostics['containerState'] = status if status in (
                        'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'
                    ) else 'running' if running else 'not-running'
                    exit_code, oom = state.get('ExitCode'), state.get('OOMKilled')
                    runtime.diagnostics['exitCode'] = exit_code if type(exit_code) is int and 0 <= exit_code <= 255 else 'unknown'
                    runtime.diagnostics['oomKilled'] = oom if type(oom) is bool else 'unknown'
                    if not running:
                        runtime.fail('container-not-running')
                with runtime.phase('docker-health'):
                    health = state.get('Health', {}).get('Status')
                    require(health in (None, 'starting', 'healthy', 'unhealthy'))
                    runtime.diagnostics['dockerHealth'] = health or 'absent'
                    if health == 'unhealthy':
                        runtime.fail('docker-health-unhealthy')
                status = None
                if component['type'] == 'http' and health != 'starting':
                    with runtime.phase('http-readiness'):
                        ingress = runtime.application.spec.get('ingress') or {}
                        # Address/response bounds remain fatal contract checks.
                        ip = runtime.ip(doc)
                        try:
                            good = request(ip, component['port'], component['health']['path'],
                                           ingress.get('hostname', name), timeout=timeout / 2)
                        except (OSError, http.client.HTTPException) as error:
                            runtime.diagnostics['httpReadiness'] = 'timeout' if isinstance(error, TimeoutError) else 'unavailable'
                            return ProbeResult(True)
                        runtime.diagnostics['httpReadiness'] = 'ready' if good else 'not-ready'
                    status = 200 if good else 503
                return ProbeResult(True, http_status=status, health=health or 'unknown')
        probe = Probe()
        result = verify_readiness(component, container_id, probe,
                                  ReadinessPolicy(deadline=120, interval=2, successes=2, probe_timeout=5))
        self.diagnostics.update(probes=result.probes, consecutive=result.consecutive)
        with self.phase('readiness'):
            if result.status != 'verified':
                self.fail('readiness-timeout' if result.reason == 'readiness-deadline' else 'readiness-failed')
        return probe.document

    def security(self, doc, name):
        pid = doc['State']['Pid']
        require(type(pid) is int and pid > 1)
        contract = self.components[name]['runtimeSecurity']
        raw = command(['sudo', '-n', 'cat', '/proc/' + str(pid) + '/status']).decode()
        fields = dict(line.split(':', 1) for line in raw.splitlines() if ':' in line)
        uid, gid = contract['user'].split(':')
        require(fields['Uid'].split() == [uid] * 4 and fields['Gid'].split() == [gid] * 4)
        require(all(int(fields[key].strip(), 16) == 0 for key in ('CapEff', 'CapPrm', 'CapBnd', 'CapAmb')))
        require(fields['NoNewPrivs'].strip() == '1')
        mounts = command(['sudo', '-n', 'cat', '/proc/' + str(pid) + '/mounts']).decode().splitlines()
        for tmpfs in contract['tmpfs']:
            actual = [line.split()[3].split(',') for line in mounts if line.split()[1] == tmpfs['target']]
            require(len(actual) == 1 and {'rw', 'noexec', 'nosuid', 'nodev',
                    'size=' + str(tmpfs['sizeBytes'] // 1024) + 'k'}.issubset(actual[0]))
        ports = doc['NetworkSettings'].get('Ports') or {}
        require(all(not bindings for bindings in ports.values()))
        require(not self.docker('diff', doc['Id']).strip())

    def verify(self, evidence):
        require(len(self.owned('container')) == len(self.components))
        documents = {name: self.healthy(name, evidence[name]) for name in self.components}
        for name, doc in documents.items():
            self.security(doc, name)
            for target in self.components[name]['dependsOn']:
                target_doc = documents[target]
                port = self.components[target].get('port', 0)
                pid = doc['State']['Pid']
                require(type(pid) is int and pid > 1)
                command(['sudo', '-n', 'nsenter', '--target', str(pid), '--net', '--', sys.executable,
                         '-c', NETWORK_PROBE, target, self.ip(target_doc), str(port)], timeout=15)
        return documents

    def restart(self, evidence):
        before = self.verify(evidence)
        self.compose('restart', *startup_order(self.application))
        after = self.verify(evidence)
        require(all(after[n]['State']['StartedAt'] != before[n]['State']['StartedAt'] for n in before))

    def cleanup(self):
        if not self.active:
            return
        for identifier in self.owned('container'):
            doc = decoded(self.docker('container', 'inspect', identifier))[0]
            self.ownership(doc, 'container')
            self.docker('container', 'rm', '--force', identifier)
        for kind in ('network', 'volume'):
            for identifier in self.owned(kind):
                doc = decoded(self.docker(kind, 'inspect', identifier))[0]
                self.ownership(doc, kind)
                require(not doc.get('Containers'))
                # Volumes exist only after collision-free preflight and explicit
                # disposable-volume consent. Never force removal or prune.
                self.docker(kind, 'rm', identifier)
        require(not any(self.owned(kind) for kind in ('container', 'network', 'volume')))


def pull_and_bind(proof, record, report):
    """Stop before runtime on any failure; persist only locally selected codes."""
    engine = record['engine'] = {'pull': 'pending', 'inspect': 'pending', 'binding': 'pending'}
    for step, stage, code in (
        ('pull', 'docker-pull', 'docker-pull-failed'),
        ('inspect', 'docker-image-inspect', 'docker-image-inspect-failed'),
        ('binding', 'engine-identity-binding', 'engine-binding-failed'),
    ):
        report['stage'] = stage
        engine[step] = 'running'
        codes = (code,)
        try:
            if step == 'pull':
                command(['docker', 'pull', '--quiet', '--platform', 'linux/amd64', proof.reference], timeout=180)
            elif step == 'inspect':
                raw = command(['docker', 'image', 'inspect', proof.reference])
                codes = ('docker-image-inspect-invalid',)
                documents = decoded(raw)
                require(isinstance(documents, list) and len(documents) == 1 and isinstance(documents[0], dict))
                inspected = documents[0]
            else:
                bind_engine_image(proof, inspected)
        except EngineBindingError as error:
            engine[step] = 'failed'
            engine['checks'] = error.checks
            report['errorCodes'] = list(error.codes)
            raise AcceptanceError() from None
        except Exception:
            engine[step] = 'failed'
            report['errorCodes'] = list(codes)
            raise AcceptanceError() from None
        engine[step] = 'passed'


def accept(application, release, artifact, directory, session, gh_binary, report):
    evidence = {}
    report['components'] = {}
    # Authenticate ALL images before any pull or runtime mutation.
    for component in application.spec['components']:
        name, image = component['name'], component['image']
        identity = release['components'][name]
        record = {'image': image, 'sourceRepository': application.source_repository,
                  'sourceSha': identity['sourceSha'], 'signerSha': identity['signerSha'],
                  'sourceRef': identity['sourceRef'], 'signerWorkflow': identity['signerWorkflow']}
        report['components'][name] = record
        report['stage'] = 'registry-bytes'
        blobs, proof = registry_blobs(image)
        record['mapping'] = {'manifest': proof.manifest_digest, 'config': proof.image_id,
                             'blobs': {d: len(raw) for d, raw in sorted(blobs.items())}}
        defaults = json.loads(proof.defaults_json)
        if defaults.get('Volumes'):
            raise AcceptanceBlocked()  # No image-declared anonymous storage.
        health = defaults.get('Healthcheck', {}).get('Test', [])
        if component['health']['strategy'] == 'imageHealthcheck' and (not health or health[0] == 'NONE'):
            raise AcceptanceBlocked()
        report['stage'] = 'signed-attestations'
        record['attestations'] = []
        for expectation in identity['attestations']:
            raw = command(attestation_command(image, application.source_repository, identity,
                                              expectation['predicateType'], gh_binary), timeout=180, gh=True)
            verified_statement(raw, image, expectation)
            record['attestations'].append({'predicateType': expectation['predicateType'],
                                           'verifiedOutputSha256': hashlib.sha256(raw).hexdigest()})
        evidence[name] = proof
    for name, proof in evidence.items():
        pull_and_bind(proof, report['components'][name], report)
    runtime = DisposableRuntime(application, artifact, directory, session)
    try:
        report['stage'] = 'runtime-start'
        runtime.start(evidence)
        report['stage'] = 'readiness-network-security'
        runtime.verify(evidence)
        report['stage'] = 'restart'
        runtime.restart(evidence)
        report['runtimeSecurity'] = 'passed'
    finally:
        # Preserve the failure diagnosis even if cleanup itself fails.
        if hasattr(runtime, 'diagnostics'):
            report['runtime'] = runtime.diagnostics
            if 'errorCode' in runtime.diagnostics:
                report['errorCodes'] = [runtime.diagnostics['errorCode']]
        report['cleanup'] = 'failed'
        runtime.cleanup()
        report['cleanup'] = 'passed'


def main():
    report = {'schema': 'portfolio.isolated-acceptance/v1', 'result': 'failed',
              'productionAdmission': False, 'stage': 'runner-gate'}
    output = ROOT / 'acceptance-evidence'
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
            app, release, artifact = candidate_model(ROOT, directory / 'registry-root',
                os.environ.get('ACCEPTANCE_APPLICATION', ''), report['managerSha'], session)
            report['application'] = app.id
            report['composeSha256'] = artifact.sha256
            report['stage'] = 'github-cli-integrity'
            gh_binary = provision_gh(directory)
            report['githubCli'] = {'version': GH_VERSION, 'archiveSha256': GH_ARCHIVE_SHA256}
            accept(app, release, artifact, directory, session, gh_binary, report)
            report.update(result='passed', stage='complete')
    except AcceptanceBlocked:
        report['result'] = 'blocked'
    except Exception:
        pass  # Never expose exception text, command output, env or logs.
    finally:
        if report_ready:
            raw = json.dumps(report, sort_keys=True, indent=2) + '\n'
            require(len(raw) <= LIMIT)
            (output / 'report.json').write_text(raw)
    print('Isolated acceptance: ' + report['result'] + ' (' + report['stage'] + '); production admission: false')
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
