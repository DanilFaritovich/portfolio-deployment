"""Installed pins -> exact Engine runtime, storage and UDS identity evidence.

Pins are reviewed fixed composition inputs, never operation/request fields.
No directory is created, repaired or adopted by observation.
"""
import re

from scripts.docker_adapter import DockerError
from scripts.production_integration.admin_contract import HOST_DIRECTORY
from scripts.production_integration.admin_socket import metadata, socket_identity
from scripts.production_integration.caddy_image import bind_caddy_engine
from scripts.production_integration.caddy_runtime import verify_caddy_runtime
from scripts.production_integration.gateway_read import GatewaySnapshot
from scripts.production_integration.observations import require
from scripts.production_integration.storage import validate_storage_directory
from pathlib import Path


class PinnedCaddyRuntime:
    def __init__(self, installation, engine, runtime, evidence, *, container_id, network_id):
        self.installation, self.engine, self.runtime, self.evidence = installation, engine, runtime, evidence
        self.container_id, self.network_id = container_id, network_id

    def inspect(self, channel):
        try:
            return self._inspect(channel)
        except (OSError, KeyError, TypeError, ValueError, AttributeError):
            raise DockerError('production-admin-runtime-unavailable') from None

    def _inspect(self, channel):
        self.installation.verify()
        require(channel.runtime is self and self.runtime.paths.admin_socket_directory == HOST_DIRECTORY and
                self.runtime.version == '2.11.7' and
                all(isinstance(pin, str) and re.fullmatch(r'[0-9a-f]{64}', pin)
                    for pin in (self.container_id, self.network_id)), 'production-admin-runtime-pin-invalid')
        identity = self.engine.identity()
        image = self.engine.inspect('image', self.runtime.image)
        bind_caddy_engine(self.runtime, self.evidence, image)
        doc = self.engine.inspect('container', self.container_id)
        require(doc.get('Id') == self.container_id and doc.get('State', {}).get('Running') is True and
                type(doc['State'].get('Pid')) is int and doc['State']['Pid'] > 1 and
                not doc['State'].get('Restarting') and not doc['State'].get('Paused') and
                not verify_caddy_runtime(self.runtime, self.runtime.render(), self.evidence, doc),
                'production-admin-effective-runtime-invalid')
        networks = doc['NetworkSettings']['Networks']
        require(networks['portfolio-proxy'].get('NetworkID') == self.network_id,
                'production-admin-network-pin-invalid')
        directories = []
        for mount in self.runtime.render().model['services']['caddy']['volumes']:
            path = mount['source']
            validate_storage_directory(path, uid=0)
            info = Path(path).lstat()
            require(info.st_gid == 0 and info.st_mode & 0o7777 == 0o700, 'production-admin-storage-mode-invalid')
            directories.append((path, metadata(info)))
        socket = socket_identity()
        require(self.engine.identity() == identity, 'production-admin-engine-changed')
        return GatewaySnapshot((), '', self.evidence.image_id, self.runtime.version, True, True, True,
                               self.container_id, (socket, doc['State']['Pid'], tuple(directories), identity))
