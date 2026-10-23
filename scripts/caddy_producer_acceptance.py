"""Exact published producer digest in the existing isolated Caddy acceptance.

Distroless has no debug tools. Trusted ephemeral runner probes join only network
namespaces; filesystem probes traverse /proc/PID/root's actual container mounts.
No helper binary, mount, capability or writable root is added to the image.
"""
import argparse
import errno
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from scripts.caddy_integration import CaddyHarness, isolated_artifact, environment_guard
from scripts.caddy_producer import IMAGE, REPOSITORY, load_lock
from scripts.docker_adapter import DockerError
from scripts.production_integration.release_transport import GHCRRead, HTTPSRead
from scripts.production_integration.runtime import CaddyRuntime
from scripts.reconciliation import encode
from scripts.runtime_integration import InjectedFailure, IntegrationFailure, require

FILESYSTEM_PROBE = ('test ! -e /var/run/docker.sock; test ! -e /run/docker.sock; '
                    'if touch /rootfs-probe 2>/dev/null; then exit 1; fi; '
                    'if touch /etc/caddy/write-probe 2>/dev/null; then exit 1; fi; '
                    'printf data-marker > /data/acceptance-marker; printf config-marker > /config/acceptance-marker')
HTTP_PROBE = '''import sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(sys.argv[1], timeout=3) as response:
    data = response.read(1048577)
    if response.status != 200 or len(data) > 1048576:
        sys.exit(1)
    sys.stdout.buffer.write(data)
'''


def producer_guard(endpoint, authorization, env=None):
    env = dict(os.environ if env is None else env)
    require(env.get('GITHUB_EVENT_NAME') == 'push' and env.get('GITHUB_REF') == 'refs/heads/main' and
            env.get('GITHUB_REF_PROTECTED') == 'true' and env.get('GITHUB_REPOSITORY') == REPOSITORY,
            'caddy-producer-protected-main-required')
    # Reuse every existing endpoint/ephemeral-host/root guard. Only the event
    # projection differs for this separately authorized protected-main workflow.
    environment_guard(endpoint, authorization, {**env, 'GITHUB_EVENT_NAME': 'pull_request'})


class ProducerHarness(CaddyHarness):
    def __init__(self, endpoint, directory, evidence):
        super().__init__(endpoint, directory)
        version = load_lock()['version']
        self.evidence = evidence
        self.lock = {'version': version, 'index': evidence.reference.split('@')[1]}
        self.runtime = CaddyRuntime(evidence.reference, version, self.runtime.paths)
        self.artifact = isolated_artifact(self.runtime, directory, self.session)

    def pid(self, container):
        doc = self.inspect('container', container)
        require(self.owned(doc, 'container') and doc['State']['Running'] is True and
                type(doc['State']['Pid']) is int and doc['State']['Pid'] > 1, 'caddy-probe-owner-invalid')
        return doc['State']['Pid']

    def http(self, container, url):
        require(re.fullmatch(r'http://[0-9.]+:(?:80|2019)/(?:config/)?', url) is not None,
                'caddy-probe-url-invalid')
        ipaddress.IPv4Address(url.split('/')[2].split(':')[0])
        result = subprocess.run(['nsenter', '--target', str(self.pid(container)), '--net', '--',
                                 sys.executable, '-c', HTTP_PROBE, url], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
        require(len(result.stdout) <= 1048576, 'caddy-probe-output-bound')
        return result.returncode, result.stdout

    def attempt(self, *args):
        if args[:3] == ('exec', self.container, 'curl'):
            return self.http(self.container, args[-1])
        if args and args[0] == 'compose' and '--entrypoint' in args:
            require(args[args.index('--entrypoint') + 1] == 'curl', 'caddy-peer-command-invalid')
            peer = self.project + '-peer'
            try:
                self.compose(self.artifact.model, 'run', '--detach', '--no-deps', '--pull', 'never',
                             '--name', peer, 'caddy')
                return self.http(peer, args[-1])
            finally:
                doc = self.inspect('container', peer)
                require(self.owned(doc, 'container'), 'caddy-peer-owner-invalid')
                self.docker('container', 'rm', '--force', peer)
        return super().attempt(*args)

    def execute(self, *args):
        if args[:1] == ('cat',):
            require(len(args) == 2 and args[1] in ('/proc/1/status', '/data/acceptance-marker',
                                                  '/config/acceptance-marker'), 'caddy-probe-path-invalid')
            pid = self.pid(self.container)
            path = Path('/proc') / str(pid)
            target = path / 'status' if args[1] == '/proc/1/status' else path / 'root' / args[1].lstrip('/')
            with target.open('rb') as stream:
                raw = stream.read(1048577)
            require(len(raw) <= 1048576, 'caddy-probe-output-bound')
            if args[1] == '/proc/1/status':
                fields = dict(line.split(':', 1) for line in raw.decode().splitlines() if ':' in line)
                require(fields['NSpid'].split()[-1] == '1', 'caddy-probe-not-pid1')
            return raw
        if args[:1] == ('sh',):
            require(args == ('sh', '-ec', FILESYSTEM_PROBE), 'caddy-probe-command-invalid')
            root = Path('/proc') / str(self.pid(self.container)) / 'root'
            require(not (root / 'var/run/docker.sock').exists() and not (root / 'run/docker.sock').exists(),
                    'caddy-probe-docker-socket-present')
            for name in ('rootfs-probe', 'etc/caddy/write-probe'):
                try:
                    with (root / name).open('xb'):
                        pass
                except OSError as error:
                    require(error.errno == errno.EROFS, 'caddy-probe-not-read-only')
                else:
                    raise IntegrationFailure('caddy-probe-not-read-only')
            (root / 'data/acceptance-marker').write_bytes(b'data-marker')
            (root / 'config/acceptance-marker').write_bytes(b'config-marker')
            return b''
        if args[:1] == ('curl',):
            code, raw = self.http(self.container, args[-1])
            require(code == 0, 'caddy-probe-http-failed')
            return raw
        raw = super().execute(*args)
        if args == ('caddy', 'validate', '--config', '/etc/caddy/candidate.Caddyfile', '--adapter', 'caddyfile'):
            # Reload only the exact already allowlisted HTTP fixture, never the
            # acceptance.invalid candidate or any production/ACME configuration.
            super().execute('caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
            self.completed.append('reload-current-http-fixture')
        return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True)
    args = parser.parse_args()
    report = {'schema': 'portfolio.caddy-acceptance/v1', 'productionAdmission': False,
              'provenance': 'pending-signed-producer-evidence', 'realDocker': False,
              'status': 'failed', 'cleanup': 'not-started', 'cases': []}
    try:
        producer_guard('unix:///var/run/docker.sock', 'disposable-caddy-integration')
        auth = ((os.environ.get('GITHUB_ACTOR', ''), os.environ['CADDY_REGISTRY_TOKEN']) if
                'CADDY_REGISTRY_TOKEN' in os.environ else None)
        evidence = GHCRRead(HTTPSRead(), repository=IMAGE, registry_auth=auth).collect(args.reference)
        from scripts.production_integration.artifacts import verify_oci_bytes
        mapped = verify_oci_bytes(args.reference, IMAGE, evidence)
        with tempfile.TemporaryDirectory(prefix='pd-caddy-producer-') as directory:
            harness = ProducerHarness('unix:///var/run/docker.sock', Path(directory), mapped)
            try:
                try:
                    harness.run()
                except InjectedFailure:
                    harness.completed.append('injected-partial-failure')
                report.update(status='passed', realDocker=True)
            finally:
                report.update(cases=harness.completed, observations=harness.observations, stage=harness.stage)
                report['cleanup'] = 'failed'
                harness.cleanup()
                report['cleanup'] = 'passed'
                report['cases'].append('cleanup-after-partial-failure')
    except (IntegrationFailure, DockerError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        report['status'] = 'failed'
        report['code'] = 'caddy-producer-acceptance-failed'
    print(encode(report))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
