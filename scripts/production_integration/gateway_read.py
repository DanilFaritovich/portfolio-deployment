"""Exact managed gateway attestation from an independently approved read port."""
from dataclasses import dataclass

from scripts.docker_adapter import DockerError
from scripts.gateway import route_set
from scripts.production_integration.gateway import content


@dataclass(frozen=True)
class GatewaySnapshot:
    """Transport must obtain ACTIVE config, not merely a disk file or LKG."""
    routes: tuple
    active_caddyfile: str
    image_config_id: str
    version: str
    runtime_verified: bool
    ownership_verified: bool
    certificates_storage_verified: bool


class ManagedGatewayReadPort:
    def __init__(self, transport, *, image_evidence, expected_version):
        self.transport = transport
        self.image = image_evidence
        self.expected_version = expected_version

    def read(self):
        sample = self.transport.read_active()
        if (not isinstance(sample, GatewaySnapshot) or self.image.verified is not True or
            sample.image_config_id != self.image.image_id or sample.version != self.expected_version or
            sample.runtime_verified is not True or sample.ownership_verified is not True or
            sample.certificates_storage_verified is not True):
            raise DockerError('production-gateway-runtime-attestation-unverified')
        routes = route_set(sample.routes)
        if not routes or sample.active_caddyfile != content(routes):
            raise DockerError('production-gateway-foreign-or-unsupported-config')
        return routes

    def compare_and_reload(self, old, new):
        raise DockerError('stage-09-4-gateway-write-authorization-unavailable')
