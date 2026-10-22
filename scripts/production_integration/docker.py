"""Local Engine production read port, reusing bounded Stage 05 inventory commands.

Construction never contacts Docker. No production write capability is issued in
Stage 09.3. SSH access is not authorization for host-root-equivalent socket access.
"""
from dataclasses import dataclass
import re
import shutil
import tempfile

from scripts.docker_adapter import DockerError, LocalDockerAdapter, run_bounded, verify_socket
from scripts.planning_inputs import parse_snapshot


@dataclass(frozen=True)
class EngineContract:
    endpoint: str
    daemon_id: str
    host_id: str
    read_authorized: bool = False

    def validate(self):
        if (self.read_authorized is not True or not isinstance(self.daemon_id, str) or
            not isinstance(self.host_id, str) or not self.daemon_id or not self.host_id or
            len(self.daemon_id) > 128 or not re.fullmatch(r'[0-9a-f]{32}', self.host_id)):
            raise DockerError('production-engine-read-approval-required')
        try:
            verify_socket(self.endpoint)
        except OSError:
            raise DockerError('production-engine-socket-unavailable') from None


class ProductionDockerAdapter:
    test_session = None
    inventory = LocalDockerAdapter.inventory
    inspect = LocalDockerAdapter.inspect

    def __init__(self, contract, host_identity):
        self.contract, self.host_identity = contract, host_identity
        self.binary = shutil.which('docker')

    def identity(self):
        self.contract.validate()
        if self.host_identity() != self.contract.host_id:
            raise DockerError('production-host-identity-mismatch')
        if not self.binary:
            raise DockerError('docker-not-installed')
        with tempfile.TemporaryDirectory(prefix='pd-production-read-') as home:
            try:
                info = parse_snapshot(run_bounded([self.binary, '--host', self.contract.endpoint,
                                                   'info', '--format', '{{json .}}'], cwd=home))
                if (info.get('ID') != self.contract.daemon_id or info.get('OSType') != 'linux' or
                    info.get('Architecture') not in ('x86_64', 'amd64')):
                    raise DockerError('production-daemon-identity-mismatch')
            except (ValueError, AttributeError, UnicodeError):
                raise DockerError('production-daemon-identity-unavailable') from None
        return self.contract.host_id, self.contract.daemon_id

    def _run(self, args, *, timeout=20, cwd=None):
        self.identity()
        # Only inherited inventory/inspect methods use this port. No arbitrary
        # mutation can cross the adapter even via a future accidental caller.
        allowed = (len(args) >= 2 and args[0] in ('container', 'network', 'volume', 'image') and
                   args[1] in ('inspect', 'ls'))
        if not allowed:
            raise DockerError('production-read-only-command-required')
        with tempfile.TemporaryDirectory(prefix='pd-production-read-') as home:
            return run_bounded([self.binary, '--host', self.contract.endpoint, *args], timeout=timeout, cwd=home)

    def authorize(self, artifact):
        raise DockerError('stage-09-4-operation-authorization-unavailable')

    def up(self, artifact, service):
        self.authorize(artifact)
