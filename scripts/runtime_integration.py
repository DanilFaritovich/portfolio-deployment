"""Opt-in real Compose/Engine contract test, on an otherwise empty disposable daemon.

No registry, source checkout, provenance shortcut or production application. The
local scratch image is identified by its Engine config ID solely in this harness.
Negative configurations are CREATED, never started (including ports/privilege).
"""
import argparse
from dataclasses import replace
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
from types import SimpleNamespace

from scripts.compose import LABEL, render_compose
from scripts.docker_discovery import DockerResource, ImageEvidence, container_runtime_verification
from scripts.reconciliation import encode

ROOT = Path(__file__).resolve().parents[1]


class IntegrationFailure(Exception):
    pass


def require(ok, code):
    if not ok:
        raise IntegrationFailure(code)


class InjectedFailure(IntegrationFailure):
    pass


class Harness:
    def __init__(self, endpoint, directory):
        self.session = secrets.token_hex(8)
        self.directory = directory
        # Never inherit Docker contexts, proxy injection or production credentials.
        self.env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}
        self.env.update(DOCKER_HOST=endpoint, DOCKER_CONFIG=str(directory / 'docker-config'))
        (directory / 'docker-config').mkdir()
        self.project = 'pd-runtime-' + self.session
        self.image_id = None
        self.completed = []
        self.observations = {}

    def docker(self, *args, stdin=None):
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['docker', *args], input=stdin, env=self.env,
                stdout=output, stderr=subprocess.DEVNULL, timeout=120, check=False)
            require(result.returncode == 0 and output.tell() <= 4 * 1024 * 1024, 'docker-command-failed')
            output.seek(0)
            return output.read()

    def inspect(self, kind, identifier):
        return json.loads(self.docker(kind, 'inspect', identifier))[0]

    def owned(self, document, kind):
        labels = document['Config']['Labels'] if kind == 'container' else document['Labels']
        return labels.get(LABEL + '.test-session') == self.session

    def fixture(self):
        require(not self.docker('ps', '-aq').strip(), 'disposable-daemon-must-have-no-containers')
        cfile = ROOT / 'tests/runtime_fixture/fixture.c'
        result = subprocess.run(['cc', '-static', '-Os', '-o', str(self.directory / 'fixture'), str(cfile)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        require(result.returncode == 0, 'static-fixture-build-failed')
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            archive.add(self.directory / 'fixture', arcname='fixture')
        # import creates only a new local image; no Dockerfile/base download.
        self.image_id = self.docker('image', 'import',
            '--change', 'USER 10001:10001', '--change', 'WORKDIR /',
            '--change', 'ENTRYPOINT ["/fixture"]', '--change', 'CMD ["serve"]',
            '--change', 'ENV FIXTURE=1', '--change', 'EXPOSE 8080',
            '--change', 'HEALTHCHECK CMD ["/fixture", "health"]',
            '--change', 'LABEL ' + LABEL + '.test-session=' + self.session, '-',
            stdin=data.getvalue()).decode().strip()

    def prepare(self):
        app = SimpleNamespace(id='fixture', spec={'components': [dict(name='web',
            image=self.image_id, platform='linux/amd64', dependsOn=[], proxyNetwork=True,
            volumeMounts=[{'volume': 'data', 'mountPath': '/data'}],
            runtimeSecurity={'apiVersion': 'portfolio.runtime/v1', 'user': '10001:10001',
                'readOnlyRootFilesystem': True, 'capDrop': ['ALL'],
                'noNewPrivileges': True, 'imageHealthcheck': 'preserve', 'restart': 'no',
                'tmpfs': [{'target': '/tmp', 'sizeBytes': 1048576, 'mode': '1777'}]})],
            'storage': {'type': 'persistent', 'volumes': [{'name': 'data'}]}})
        self.artifact = render_compose(app, test_session=self.session)
        self.project = self.artifact.project
        self.file = self.directory / 'compose.json'
        inspected = self.inspect('image', self.image_id)
        self.evidence = ImageEvidence(self.image_id, self.image_id, 'linux/amd64', None, True, encode(inspected['Config']))
        proxy = self.artifact.model['networks']['proxy']['name']
        self.docker('network', 'create', '--internal', '--label', LABEL + '.test-session=' + self.session, proxy)

    def compose(self, model, *args):
        self.file.write_text(encode(model))
        return self.docker('compose', '--project-name', self.project, '--file', str(self.file), *args)

    def verify(self, doc, evidence=None):
        facts = {'config': doc['Config'], 'host': doc['HostConfig'], 'imageId': doc['Image'],
                 'networks': doc['NetworkSettings']['Networks'], 'mounts': doc['Mounts']}
        resource = DockerResource('container', doc['Id'], doc['Name'].lstrip('/'), encode(facts))
        return container_runtime_verification(resource, self.artifact, 'web', evidence or self.evidence)

    def run_case(self, name, mutate=None, expected=None, start=False):
        model = self.artifact.model
        if mutate:
            mutate(model)
        # Unique container per case, immutable original expectation.
        container_name = self.project + '-' + name
        model['services']['web']['container_name'] = container_name
        self.compose(model, *(['up', '-d'] if start else ['create']), '--no-build', '--pull', 'never')
        doc = self.inspect('container', container_name)
        require(self.owned(doc, 'container'), 'ownership-failed')
        result = self.verify(doc)
        pairs = {(m.group, m.field) for m in result.mismatches}
        require(result.matched if expected is None else expected in pairs, 'contract-case-failed:' + name + ':' + encode(result.diagnostic()))
        if start:
            self.observations = {
                'tmpfsConfiguredInHostConfig': bool(doc['HostConfig'].get('Tmpfs')),
                'tmpfsPresentInMounts': any(m.get('Type') == 'tmpfs' for m in doc['Mounts']),
                'primaryNetworkDeclared': doc['HostConfig'].get('NetworkMode') in doc['NetworkSettings']['Networks'],
            }
            require(doc['State']['Running'] is True, 'fixture-not-running')
            # Wrong expected binding is checked against a REAL inspect, never a
            # fabricated modified inspect. Identity is independent of Config.Image.
            wrong = replace(self.evidence, image_id='sha256:' + '0' * 64)
            require(any(m.field == 'ImageID' for m in self.verify(doc, wrong).mismatches), 'wrong-image-id-accepted')
            self.completed.append('wrong-image-id')
            self.completed.append('docker-managed-defaults')
        self.completed.append(name)
        self.docker('container', 'rm', '--force', doc['Id'])

    def run(self):
        self.fixture()
        self.prepare()
        self.run_case('valid', start=True)
        def setting(key, value):
            return lambda m: m['services']['web'].update({key: value})
        cases = [
            ('user', 'user', '0:0', ('config', 'User')),
            ('entrypoint', 'entrypoint', ['/fixture', 'changed'], ('config', 'Entrypoint')),
            ('cmd', 'command', ['changed'], ('config', 'Cmd')),
            ('env', 'environment', {'FIXTURE': 'changed'}, ('config', 'Env')),
            ('healthcheck', 'healthcheck', {'disable': True}, ('config', 'Healthcheck')),
            ('privileged', 'privileged', True, ('security', 'Privileged')),
            ('capabilities', 'cap_add', ['SYS_ADMIN'], ('security', 'CapAdd')),
            ('cap-drop', 'cap_drop', [], ('security', 'CapDrop')),
            ('security-opt', 'security_opt', ['seccomp=unconfined'], ('security', 'SecurityOpt')),
            ('rootfs', 'read_only', False, ('security', 'ReadonlyRootfs')),
            # Never started: this asserts PortBindings without publishing ports.
            ('ports', 'ports', ['127.0.0.1::8080'], ('security', 'PortBindings')),
            ('tmpfs', 'tmpfs', ['/tmp:rw,size=1048576'], ('mounts', 'Tmpfs')),
            ('restart', 'restart', 'always', ('security', 'RestartPolicy')),
        ]
        for name, key, value, expected in cases:
            self.run_case(name, setting(key, value), expected)
        def extra_network(m):
            name = self.project + '-extra'
            m['networks']['extra'] = {'name': name, 'internal': True,
                'labels': {LABEL + '.test-session': self.session}}
            m['services']['web']['networks']['extra'] = {}
        self.run_case('extra-network', extra_network, ('networks', 'Networks'))
        def extra_mount(m):
            name = self.project + '-extra'
            m['volumes']['extra'] = {'name': name, 'labels': {LABEL + '.test-session': self.session}}
            m['services']['web']['volumes'].append({'type': 'volume', 'source': 'extra', 'target': '/extra'})
        self.run_case('extra-mount', extra_mount, ('mounts', 'Mounts'))
        # Force an application-side failure after a real Compose create. Finally
        # cleanup must still find and remove this owned container.
        self.compose(self.artifact.model, 'create', '--no-build', '--pull', 'never')
        ids = self.docker('ps', '-aq', '--filter', 'label=' + LABEL + '.test-session=' + self.session).decode().split()
        require(bool(ids), 'cleanup-fixture-absent')
        raise InjectedFailure('injected-after-create')

    def cleanup(self):
        # Inventory is session-filtered, then inspected again before each remove.
        for kind in ('container', 'network', 'volume'):
            args = ['ps', '-aq'] if kind == 'container' else [kind, 'ls', '-q']
            ids = self.docker(*args, '--filter', 'label=' + LABEL + '.test-session=' + self.session).decode().split()
            for identifier in ids:
                doc = self.inspect(kind, identifier)
                require(self.owned(doc, kind), 'cleanup-ownership-failed')
                if kind != 'container':
                    require(not doc.get('Containers'), 'cleanup-network-not-empty')
                self.docker(kind, 'rm', *(['--force'] if kind == 'container' else []), identifier)
            require(not self.docker(*args, '--filter', 'label=' + LABEL + '.test-session=' + self.session).strip(), 'cleanup-incomplete')
        if self.image_id:
            doc = self.inspect('image', self.image_id)
            require(doc['Config']['Labels'].get(LABEL + '.test-session') == self.session, 'image-cleanup-ownership-failed')
            self.docker('image', 'rm', self.image_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--authorize', required=True, choices=['disposable-runtime-integration'])
    args = parser.parse_args(argv)
    if not re.fullmatch(r'unix:///[/a-zA-Z0-9_.-]+', args.endpoint) or '..' in args.endpoint.split('/'):
        parser.error('explicit local Unix socket required')
    try:
        with tempfile.TemporaryDirectory(prefix='pd-runtime-') as directory:
            harness = Harness(args.endpoint, Path(directory))
            try:
                harness.run()
            except InjectedFailure:
                harness.completed.append('cleanup-after-error')
            finally:
                harness.cleanup()
            print(encode({'realEngine': True, 'cases': harness.completed, 'cleanup': 'passed', 'observations': harness.observations}))
        return 0
    except (IntegrationFailure, OSError, ValueError, subprocess.SubprocessError) as error:
        # Only our fixed case diagnostics, never subprocess errors/inspect values.
        print(str(error) if isinstance(error, IntegrationFailure) else 'runtime-integration-unavailable', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
