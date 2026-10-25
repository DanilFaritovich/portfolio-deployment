"""Fixed local mutations; no shell, endpoints, argv or paths from requests."""
import os
from pathlib import Path
import tempfile

from scripts.compose import LABEL, labels, render_compose
from scripts.docker_adapter import DockerError, run_bounded, scoped_compose_up
from scripts.production_integration.docker import ProductionDockerAdapter
from scripts.production_integration.gateway import content
from scripts.production_integration.storage import validate_storage_directory
from scripts.production_integration.candidate import hashed


class ScopedProductionDocker(ProductionDockerAdapter):
    """Inherited bounded reads, exact renderer equality for every Compose write.

    Installation pins Docker and its Compose plugin in protected system paths.
    No image pull is implicit: OCI/source verification and preloading are separate
    prerequisites. Reuses the Stage 05 exact config/up sequence and options.
    """
    def __init__(self, contract, host_identity, *, installation, grant, application, rollback_application=None):
        super().__init__(contract, host_identity)
        self.binary = '/usr/bin/docker'
        self.installation, self.grant, self.application = installation, grant, application
        self.rollback_application = rollback_application

    def authorize(self, artifact):
        self.installation.verify()
        self.grant.require()
        allowed = [render_compose(self.application)]
        if self.grant.candidate.scope == 'update-project-router' and self.rollback_application is not None:
            allowed.append(render_compose(self.rollback_application))
        if (self.grant.candidate.scope not in ('first-project-router-rollout', 'update-project-router') or
            self.application.id != 'project-router' or artifact not in allowed or
            self.application.spec['components'][0]['image'] != self.grant.candidate.image):
            raise DockerError('production-compose-scope-mismatch')
        self.identity()

    def up(self, artifact, service):
        self.authorize(artifact)
        if service not in artifact.model['services']:
            raise DockerError('production-service-scope-mismatch')
        compose_up(artifact, service, self.grant, self.installation)


def compose_up(artifact, service, grant, installation):
    grant.require()
    installation.verify()
    # Private directory and env stripping prevent .env, credential helper,
    # context and user Compose plugin injection. No build/pull/down/prune API.
    def run(args, **kwargs):
        grant.require()
        installation.verify()
        return run_bounded(['/usr/bin/docker', '--host', 'unix:///run/docker.sock', *args], **kwargs)
    scoped_compose_up(artifact, service, run)


class ManagedBootstrapPort:
    """Concrete resource actions, with independently installed runtime attestor.

    attestor.preflight verifies all external prerequisites, exact Caddy signature,
    runtime acceptance, storage and official local empty-config validation. It
    returns the exact PreparedOperation, never a bool. verify_created performs
    bounded repeated observation of created resource IDs, effective IPAM/runtime,
    listeners, storage and empty active config, returning ReadinessEvidence.
    No attestor is installed by default. Unknown results cannot invoke cleanup.
    """
    def __init__(self, *, installation, caddy, proxy, private_subnet, attestor=None, engine=None):
        self.installation, self.caddy, self.proxy = installation, caddy, proxy
        self.private_subnet, self.attestor = private_subnet, attestor
        self.engine = engine

    def _identity(self, grant):
        if self.engine is None or self.engine.identity() != (grant.candidate.host, grant.candidate.daemon):
            raise DockerError('production-bootstrap-engine-identity-unverified')

    def preflight(self, prepared, grant):
        self.installation.verify()
        grant.require()
        self._identity(grant)
        from scripts.planning_inputs import parse_snapshot
        from dataclasses import asdict
        contract = parse_snapshot(prepared.resource_contract.encode())
        if (hashed(self.caddy.render().model) != grant.candidate.runtime_hash or
            contract['proxy'] != asdict(self.proxy) or contract['privateSubnet'] != self.private_subnet):
            raise DockerError('production-bootstrap-resource-contract-mismatch')
        if (self.attestor is None or self.attestor.preflight(prepared, self.caddy) != prepared):
            raise DockerError('production-bootstrap-resource-attestor-unavailable')
        for path in (self.caddy.paths.gateway_config, self.caddy.paths.certificate_data, self.caddy.paths.gateway_data,
                     self.caddy.paths.admin_socket_directory):
            validate_storage_directory(path, uid=os.geteuid())
        # Never adopt a preexisting listener or foreign file as initial state.
        if any(Path(self.caddy.paths.admin_socket_directory).iterdir()):
            raise DockerError('production-bootstrap-admin-directory-not-empty')
        # Infrastructure provisions directories; bootstrap refuses existing config.
        if (Path(self.caddy.paths.gateway_config) / 'Caddyfile').exists():
            raise DockerError('production-bootstrap-config-already-present')

    def apply(self, action, prepared, grant):
        grant.require()
        self.installation.verify()
        self._identity(grant)
        if grant.candidate.scope != 'first-bootstrap' or action not in prepared.bootstrap.operations:
            raise DockerError('production-bootstrap-action-out-of-scope')
        if action == 'create-manager-proxy-with-reviewed-ipam':
            self._network('portfolio-proxy', self.proxy.subnet, self.proxy.gateway,
                          {LABEL + '.managed': 'true', LABEL + '.role': 'proxy'}, False)
        elif action == 'create-application-private-network':
            owner = {**labels('project-router'), 'com.docker.compose.project': 'portfolio-project-router',
                     'com.docker.compose.network': 'private'}
            self._network('portfolio-project-router-private', self.private_subnet, None, owner, True)
        elif action == 'initialize-private-journal-and-storage':
            target = Path(self.caddy.paths.gateway_config) / 'Caddyfile'
            # O_EXCL prevents adoption/overwrite; crash leaves diagnostic state.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                raw = content(()).encode()
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except BaseException:
                raise DockerError('production-bootstrap-storage-outcome-unknown', True) from None
        elif action == 'officially-validate-empty-caddy-candidate':
            self.attestor.validate_empty(self.caddy, content(()))
        elif action == 'create-managed-caddy-with-empty-config':
            compose_up(self.caddy.render(), 'caddy', grant, self.installation)
        else:
            raise DockerError('production-bootstrap-action-unsupported')

    def _network(self, name, subnet, gateway, owner, internal):
        argv = ['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'network', 'create',
                '--driver', 'bridge', '--subnet', subnet]
        if gateway:
            argv.extend(['--gateway', gateway])
        if internal:
            argv.append('--internal')
        for key, value in sorted(owner.items()):
            argv.extend(['--label', key + '=' + value])
        argv.append(name)
        with tempfile.TemporaryDirectory(prefix='pd-production-network-') as directory:
            run_bounded(argv, cwd=directory)

    def verify(self, prepared, grant):
        grant.require()
        return self.attestor.verify_created(prepared, self.caddy)
