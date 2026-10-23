"""Explicit disposable GitHub-hosted Caddy acceptance; never a production port."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from scripts.compose import LABEL, ComposeArtifact
from scripts.gateway import make_route
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import blob_digest, map_oci_bytes, EngineBindingError
from scripts.production_integration.caddy_image import inspect_caddy_contract, bind_caddy_engine
from scripts.production_integration.caddy_runtime import verify_caddy_runtime
from scripts.production_integration.gateway import content
from scripts.production_integration.admin_contract import CONTAINER_ADDRESS, CONTAINER_DIRECTORY
from scripts.production_integration.admin_socket import _read_socket
from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths
from scripts.reconciliation import encode
from scripts.runtime_integration import Harness, IntegrationFailure, InjectedFailure, require

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/caddy'
AUTHORIZATION = 'disposable-caddy-integration'


def environment_guard(endpoint, authorization, env=None):
    env = os.environ if env is None else env
    require(authorization == AUTHORIZATION, 'caddy-disposable-authorization-required')
    require(endpoint == 'unix:///var/run/docker.sock' and
            env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
            env.get('RUNNER_OS') == 'Linux' and env.get('RUNNER_ARCH') == 'X64' and
            env.get('GITHUB_EVENT_NAME') == 'pull_request' and
            env.get('GITHUB_REPOSITORY') == 'DanilFaritovich/portfolio-deployment' and
            platform.system() == 'Linux' and platform.machine() == 'x86_64' and os.geteuid() == 0,
            'caddy-disposable-github-runner-required')


def image_evidence(directory=FIXTURES):
    lock = json.loads((directory / 'image-lock.json').read_bytes())
    require(lock['repository'] == 'docker.io/library/caddy' and lock['platform'] == 'linux/amd64', 'caddy-lock-invalid')
    blobs = {blob_digest(raw): raw for raw in ((directory / (name + '.oci')).read_bytes()
                                             for name in ('index', 'manifest', 'config'))}
    reference = lock['repository'] + '@' + lock['index']
    evidence = map_oci_bytes(reference, lock['repository'], blobs)
    require(evidence.manifest_digest == lock['manifest'] and evidence.image_id == lock['config'], 'caddy-lock-mapping-mismatch')
    return lock, evidence


def isolated_artifact(runtime, directory, session):
    """Only resource identities/paths and host publishing differ from production."""
    model = runtime.render().model
    project = 'pd-caddy-' + session
    model['name'] = project
    service = model['services']['caddy']
    service['ports'] = []  # Container 80 only; no host listener, including 443/admin.
    service['networks'] = {'proxy': {}}
    service['labels'][LABEL + '.test-session'] = session
    for mount in service['volumes']:
        mount['source'] = str(directory / mount['target'].lstrip('/').replace('/', '-'))
    model['networks']['proxy']['name'] = project + '-proxy'
    return ComposeArtifact('@gateway', project, encode(model))


def test_configuration():
    # Existing production global/admin renderer, plus a deliberately HTTP-only
    # fixture. No hostname, public DNS, TLS automation or ACME issuance.
    return content(()).replace('admin ' + CONTAINER_ADDRESS, 'admin ' + CONTAINER_ADDRESS + '\n    auto_https off') + '\n:80 {\n    respond "caddy-acceptance" 200\n}\n'


def validate_test_configuration(text):
    # Official Caddy syntax validation alone permits insecure admin/listener
    # choices. Only this exact HTTP fixture is executable in acceptance.
    require(text == test_configuration(), 'caddy-test-config-not-allowlisted')


class CaddyHarness(Harness):
    def __init__(self, endpoint, directory):
        super().__init__(endpoint, directory)
        self.lock, self.evidence = image_evidence()
        self.runtime = CaddyRuntime(self.evidence.reference, self.lock['version'], RuntimePaths(
            '/srv/portfolio/state', '/srv/portfolio/locks', '/srv/portfolio/journal',
            '/srv/portfolio/caddy-config', '/srv/portfolio/certificates', '/srv/portfolio/caddy-data', 104857600))
        self.artifact = isolated_artifact(self.runtime, directory, self.session)
        self.project = self.artifact.project
        self.file = directory / 'compose.json'
        self.container = self.project + '-caddy-1'
        self.stage = 'preflight'

    def attempt(self, *args):
        # No shell interpolation, inherited Docker environment or raw diagnostics.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['docker', *args], env=self.env, stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=30, check=False)
            require(output.tell() <= 1048576, 'caddy-command-output-bound')
            output.seek(0)
            return result.returncode, output.read()

    def execute(self, *args):
        return self.docker('exec', self.container, *args)

    def ready(self):
        for _ in range(30):
            code, body = self.attempt('exec', self.container, 'curl', '--noproxy', '*', '-fsS', '--max-time', '1', 'http://127.0.0.1:80/')
            if code == 0 and body == b'caddy-acceptance':
                return
            time.sleep(1)
        raise IntegrationFailure('caddy-readiness-failed')

    def verify(self, doc):
        return verify_caddy_runtime(self.runtime, self.artifact, self.evidence, doc)

    def negative_runtime(self, name, mutate, expected):
        model = self.artifact.model
        model['services']['caddy']['container_name'] = self.project + '-' + name
        mutate(model['services']['caddy'])
        # Create only: never execute an unsafe configuration.
        self.compose(model, 'create', '--no-build', '--pull', 'never')
        doc = self.inspect('container', self.project + '-' + name)
        require(self.owned(doc, 'container') and expected in self.verify(doc), 'caddy-negative-case-failed')
        self.docker('container', 'rm', doc['Id'])
        self.completed.append('reject-' + name)

    def active_acceptance(self, doc, config, fixture):
        # Host GET through the actual bind-mounted UDS. No helper in the image.
        mount = next(m for m in self.artifact.model['services']['caddy']['volumes']
                     if m['target'] == CONTAINER_DIRECTORY)
        path = Path(mount['source']) / 'admin.sock'
        from scripts.production_integration.admin_socket import socket_metadata
        socket_metadata(path.lstat())
        pid = doc['State']['Pid']
        def active():
            return json.loads(_read_socket(str(path), pid, timeout=1, limit=1048576))
        def adapted(name):
            return json.loads(self.execute('caddy', 'adapt', '--config', '/etc/caddy/' + name, '--adapter', 'caddyfile'))
        first = active()
        require(first == adapted('Caddyfile') and first['admin']['listen'] == CONTAINER_ADDRESS,
                'caddy-active-adapt-mismatch')
        before = path.lstat()
        # Same approved endpoint; HTTP-only fixture, zero published ports/ACME.
        (config / 'reload.Caddyfile').write_text(fixture.replace('caddy-acceptance', 'caddy-reloaded'))
        self.execute('caddy', 'reload', '--address', CONTAINER_ADDRESS,
                     '--config', '/etc/caddy/reload.Caddyfile', '--adapter', 'caddyfile')
        second = active()
        require(second != first and second == adapted('reload.Caddyfile') and
                path.lstat().st_ino == before.st_ino, 'caddy-active-reload-mismatch')
        code, _ = self.attempt('exec', self.container, 'caddy', 'reload', '--address', CONTAINER_ADDRESS,
                              '--config', '/etc/caddy/invalid.Caddyfile', '--adapter', 'caddyfile')
        require(code != 0 and active() == second, 'caddy-invalid-reload-active-changed')
        # Adapt succeeds, but Caddy cannot provision a logger on its read-only
        # config mount. This exercises a real /load failure, not just CLI parsing.
        (config / 'reload-failed.Caddyfile').write_text(fixture.replace(
            '    auto_https off', '    auto_https off\n    log {\n        output file /etc/caddy/reload-forbidden.log\n    }'))
        adapted('reload-failed.Caddyfile')
        code, _ = self.attempt('exec', self.container, 'caddy', 'reload', '--address', CONTAINER_ADDRESS,
                              '--config', '/etc/caddy/reload-failed.Caddyfile', '--adapter', 'caddyfile')
        require(code != 0 and active() == second, 'caddy-failed-load-active-changed')

        self.execute('caddy', 'reload', '--address', CONTAINER_ADDRESS,
                     '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        require(active() == first, 'caddy-active-restore-unverified')
        self.observations['activeAdminChannel'] = 'AF_UNIX-HTTP/1.1-GET-config'
        self.completed.append('permissioned-uds-active-equals-adapt-reload-invalid-preserves-active')

    def run(self):
        require(not self.docker('ps', '-aq').strip(), 'caddy-disposable-daemon-not-empty')
        info = json.loads(self.docker('info', '--format', '{{json .}}'))
        require(info['OSType'] == 'linux' and info['Architecture'] in ('x86_64', 'amd64') and
                not any('rootless' in v for v in info.get('SecurityOptions', [])), 'caddy-engine-scope-invalid')
        self.observations = {'engineVersion': info['ServerVersion'],
                             'composeVersion': self.docker('compose', 'version', '--short').decode().strip()}
        self.stage = 'image-binding'
        inspect_caddy_contract(self.runtime, self.evidence)
        self.stage = 'image-pull'
        self.docker('pull', '--platform', 'linux/amd64', self.evidence.reference)
        self.stage = 'image-inspection'
        image = self.inspect('image', self.evidence.reference)
        self.stage = 'image-binding'
        try:
            bind_caddy_engine(self.runtime, self.evidence, image)
        except EngineBindingError as error:
            self.observations['bindingCodes'] = list(error.codes)
            self.observations['bindingChecks'] = error.checks
            self.observations['shortNameRepoDigestPresent'] = ('caddy@' + self.lock['index']) in (image.get('RepoDigests') or [])
            raise
        self.stage = 'binary-version'
        version = self.docker('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--cap-add', 'NET_BIND_SERVICE', '--security-opt', 'no-new-privileges:true',
            '--label', LABEL + '.test-session=' + self.session, self.evidence.reference, 'caddy', 'version').decode().split()
        require(version and version[0] == 'v' + self.lock['version'], 'caddy-binary-version-mismatch')
        self.observations['caddyVersion'] = self.lock['version']
        self.completed.append('oci-engine-defaults-binding')
        for mount in self.artifact.model['services']['caddy']['volumes']:
            Path(mount['source']).mkdir(mode=0o700)
        config = self.directory / 'etc-caddy'
        fixture = test_configuration()
        validate_test_configuration(fixture)
        (config / 'Caddyfile').write_text(fixture)
        (config / 'bootstrap.Caddyfile').write_text(content(()))
        (config / 'candidate.Caddyfile').write_text(content((make_route('acceptance',
            {'component': 'web', 'hostname': 'acceptance.invalid', 'port': 8080}),)))
        (config / 'invalid.Caddyfile').write_text('{ invalid_directive }\n')
        self.docker('network', 'create', '--internal', '--label', LABEL + '.test-session=' + self.session,
                    self.artifact.model['networks']['proxy']['name'])
        self.observations['privateMountOwnershipMatchesImage'] = all(
            Path(m['source']).stat().st_uid == 0 and Path(m['source']).stat().st_gid == 0 and
            Path(m['source']).stat().st_mode & 0o777 == 0o700
            for m in self.artifact.model['services']['caddy']['volumes'])
        require(self.observations['privateMountOwnershipMatchesImage'], 'caddy-private-storage-owner-mismatch')
        self.stage = 'compose-validation'
        self.compose(self.artifact.model, 'config', '--quiet')
        self.stage = 'startup'
        self.compose(self.artifact.model, 'up', '-d', '--no-build', '--pull', 'never')
        self.stage = 'initial-readiness'
        self.ready()
        doc = self.inspect('container', self.container)
        codes = self.verify(doc)
        self.observations['runtimeMismatchCodes'] = list(codes)
        require(not codes, 'caddy-real-runtime-mismatch')
        self.completed.append('compose-engine-security-mounts-network')
        self.stage = 'process'
        status = self.execute('cat', '/proc/1/status').decode()
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        require(fields['Name'].strip() == 'caddy' and fields['Uid'].split() == ['0'] * 4 and
                fields['Gid'].split() == ['0'] * 4 and fields['NoNewPrivs'].strip() == '1' and
                fields['Seccomp'].strip() == '2' and all(int(fields[k].strip(), 16) == 0x400
                for k in ('CapEff', 'CapPrm', 'CapBnd')) and int(fields['CapAmb'].strip(), 16) == 0,
                'caddy-process-security-mismatch')
        self.completed.append('pid1-uid-gid-capabilities-nnp-seccomp-http80')
        self.stage = 'filesystem'
        self.execute('sh', '-ec', 'test ! -e /var/run/docker.sock; test ! -e /run/docker.sock; '
                     'if touch /rootfs-probe 2>/dev/null; then exit 1; fi; '
                     'if touch /etc/caddy/write-probe 2>/dev/null; then exit 1; fi; '
                     'printf data-marker > /data/acceptance-marker; printf config-marker > /config/acceptance-marker')
        self.completed.append('read-only-root-and-config-no-docker-socket')
        self.stage = 'admin'
        self.active_acceptance(doc, config, fixture)
        self.stage = 'validation'
        for name in ('Caddyfile', 'bootstrap.Caddyfile', 'candidate.Caddyfile'):
            self.execute('caddy', 'validate', '--config', '/etc/caddy/' + name, '--adapter', 'caddyfile')
        code, _ = self.attempt('exec', self.container, 'caddy', 'validate', '--config', '/etc/caddy/invalid.Caddyfile', '--adapter', 'caddyfile')
        require(code != 0, 'caddy-invalid-config-accepted')
        self.ready()
        self.completed.append('official-validation-and-invalid-candidate-rejection')
        self.stage = 'restart'
        self.docker('container', 'restart', self.container)
        self.ready()
        require(not self.verify(self.inspect('container', self.container)), 'caddy-restart-runtime-mismatch')
        require(self.execute('cat', '/data/acceptance-marker') == b'data-marker' and
                self.execute('cat', '/config/acceptance-marker') == b'config-marker' and
                (self.directory / 'config/caddy/autosave.json').is_file(), 'caddy-storage-not-persistent')
        self.completed.append('restart-readiness-data-config-autosave-persistence')
        self.docker('container', 'rm', '--force', self.container)
        self.stage = 'negative-runtime'
        for name, key, value, expected in (
            ('extra-capability', 'cap_add', ['NET_BIND_SERVICE', 'SYS_ADMIN'], 'caddy-runtime-capabilities-mismatch'),
            ('missing-cap-drop', 'cap_drop', [], 'caddy-runtime-security-mismatch'),
            ('writable-root', 'read_only', False, 'caddy-runtime-security-mismatch'),
            ('missing-nnp', 'security_opt', [], 'caddy-runtime-security-mismatch'),
            ('privileged', 'privileged', True, 'caddy-runtime-isolation-mismatch'),
            ('admin-published', 'ports', ['127.0.0.1::2019/tcp'], 'caddy-runtime-ports-mismatch')):
            self.negative_runtime(name, lambda s, k=key, v=value: s.update({k: v}), expected)
        self.negative_runtime('extra-mount', lambda s: s['volumes'].append(
            {'type': 'bind', 'source': str(self.directory), 'target': '/extra', 'read_only': True}), 'caddy-runtime-mounts-mismatch')
        self.negative_runtime('writable-config', lambda s: s['volumes'][0].update(read_only=False), 'caddy-runtime-mounts-mismatch')
        self.stage = 'partial-failure'
        self.compose(self.artifact.model, 'create', '--no-build', '--pull', 'never')
        require(self.owned(self.inspect('container', self.container), 'container'), 'caddy-failure-fixture-absent')
        raise InjectedFailure('caddy-injected-after-create')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--authorize', required=True)
    args = parser.parse_args(argv)
    report = {'schema': 'portfolio.caddy-acceptance/v1', 'productionAdmission': False,
              'provenance': 'unverified-production-blocker', 'realDocker': False,
              'status': 'failed', 'cleanup': 'not-started', 'cases': [], 'stage': 'environment'}
    try:
        environment_guard(args.endpoint, args.authorize)
        with tempfile.TemporaryDirectory(prefix='pd-caddy-') as directory:
            harness = CaddyHarness(args.endpoint, Path(directory))
            try:
                try:
                    harness.run()
                except InjectedFailure:
                    harness.completed.append('injected-partial-failure')
                report.update(status='passed', realDocker=True)
            except (IntegrationFailure, DockerError):
                report['stage'] = harness.stage
                raise
            finally:
                report['cases'] = harness.completed
                report['observations'] = harness.observations
                report['realDocker'] = report['realDocker'] or bool(harness.observations.get('engineVersion'))
                report['stage'] = harness.stage if report['status'] != 'passed' else 'cleanup'
                report['cleanup'] = 'failed'
                harness.cleanup()
                report['cleanup'] = 'passed'
        report['cases'].append('cleanup-after-partial-failure')
        report['stage'] = 'complete'
    except (IntegrationFailure, DockerError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        report['status'] = 'failed'
        report['code'] = 'caddy-integration-failed'
    print(encode(report))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
