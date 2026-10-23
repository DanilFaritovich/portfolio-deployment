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
    container_id: str | None = None


class ManagedGatewayReadPort:
    def __init__(self, transport, *, image_evidence, expected_version, attestor=None):
        self.transport = transport
        self.image = image_evidence
        self.expected_version = expected_version
        self.attestor = attestor

    def read(self):
        if self.attestor is None:
            raise DockerError('production-gateway-authenticated-attestor-unavailable')
        sample = self.transport.read_active()
        if self.attestor.verify(self.transport, self.image, self.expected_version, sample) != sample:
            raise DockerError('production-gateway-channel-or-runtime-unverified')
        if (not isinstance(sample, GatewaySnapshot) or self.image.verified is not True or
            sample.image_config_id != self.image.image_id or sample.version != self.expected_version or
            sample.runtime_verified is not True or sample.ownership_verified is not True or
            sample.certificates_storage_verified is not True):
            raise DockerError('production-gateway-runtime-attestation-unverified')
        routes = route_set(sample.routes)
        if sample.active_caddyfile != content(routes):
            raise DockerError('production-gateway-foreign-or-unsupported-config')
        self.last_snapshot = sample
        return routes

    def compare_and_reload(self, old, new):
        raise DockerError('stage-09-4-gateway-write-authorization-unavailable')
