"""Granted, fixed-path Caddy validation/reload transport for ProductionGateway."""
import os
from pathlib import Path
import re
import tempfile

from scripts.docker_adapter import DockerError, run_bounded
from scripts.gateway import desired_routes, make_route, route_set
from scripts.production_integration.gateway import ProductionGateway, content
from scripts.production_integration.installation import protected
from scripts.production_integration.storage import validate_storage_directory


def durable_replace(directory, name, raw):
    """Private same-directory atomic publication; retain old data on write error."""
    validate_storage_directory(str(directory), uid=os.geteuid())
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix='.candidate-', dir=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, Path(directory) / name)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)  # only our private temporary file, never lock/data


class GatewayExecutionFactory:
    def __init__(self, installation, read_port, engine=None):
        self.installation, self.read_port, self.engine = installation, read_port, engine

    def for_operation(self, grant, prepared):
        return ProductionGateway(GrantedGatewayPort(self.installation, self.read_port, grant, prepared, self.engine))


class GrantedGatewayPort:
    def __init__(self, installation, read_port, grant, prepared, engine=None):
        self.installation, self.read_port, self.grant = installation, read_port, grant
        self.engine = engine
        before = route_set(make_route(a['id'], a['route']['configuration']) for a in prepared.observed.document['applications']
                           if a['route']['existence'] == 'present')
        target = next(r for r in desired_routes(prepared.desired) if r.application == 'project-router')
        self.old = content(before)
        self.new = content(route_set([r for r in before if r.application != 'project-router'] + [target]))
        self.validated, self.preserved = False, False

    def _require(self):
        self.installation.verify()
        self.grant.require()
        if self.engine is None or self.engine.identity() != (self.grant.candidate.host, self.grant.candidate.daemon):
            raise DockerError('production-gateway-engine-identity-unverified')
        if self.grant.candidate.scope != 'first-project-router-rollout':
            raise DockerError('production-gateway-scope-invalid')

    def read(self):
        self._require()
        return self.read_port.read()

    def validate(self, text):
        self._require()
        if text != self.new:
            raise DockerError('production-gateway-candidate-mismatch')
        binary = '/opt/portfolio-deployment/tools/caddy'
        protected(binary)
        # The inventory must pin the official validation executable as well.
        if binary not in dict(self.installation.files):
            raise DockerError('production-caddy-validation-pin-required')
        with tempfile.TemporaryDirectory(prefix='pd-caddy-validate-') as directory:
            candidate = Path(directory) / 'Caddyfile'
            candidate.write_text(text)
            for action in ('adapt', 'validate'):
                run_bounded([binary, action, '--config', str(candidate), '--adapter', 'caddyfile'],
                            timeout=15, cwd=directory)
        self.validated = True

    def preserve_lkg(self, text):
        self._require()
        if text != self.old or not self.validated:
            raise DockerError('production-gateway-lkg-mismatch')
        # Per-authorization receipt is never overwritten by another operation.
        directory = '/srv/portfolio/caddy-lkg'
        validate_storage_directory(directory, uid=os.geteuid())
        name = self.grant.claims.nonce + '.Caddyfile'
        path = Path(directory) / name
        if path.exists() or path.is_symlink():
            raise DockerError('production-gateway-lkg-already-present')
        durable_replace(directory, name, text.encode())
        self.preserved = True

    def compare_and_reload(self, old, new):
        self._require()
        if (old, new) != (self.old, self.new) or not self.validated or not self.preserved or content(self.read()) != old:
            raise DockerError('production-gateway-cas-failed')
        identity = self.read_port.last_snapshot.container_id
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
            raise DockerError('production-gateway-container-identity-unverified')
        active_file = Path('/srv/portfolio/caddy-config/Caddyfile')
        protected(active_file)
        if active_file.read_bytes() != old.encode():
            raise DockerError('production-gateway-disk-state-changed')
        durable_replace('/srv/portfolio/caddy-config', 'Caddyfile', new.encode())
        self._require()
        with tempfile.TemporaryDirectory(prefix='pd-caddy-reload-') as directory:
            run_bounded(['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'exec', identity,
                         'caddy', 'reload', '--address', '127.0.0.1:2019', '--config', '/etc/caddy/Caddyfile',
                         '--adapter', 'caddyfile'], timeout=20, cwd=directory)
