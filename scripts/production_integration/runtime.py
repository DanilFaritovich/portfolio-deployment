"""Explicit operator contracts and deterministic managed gateway projection."""
from dataclasses import dataclass
import ipaddress
from pathlib import PurePosixPath
import re

from scripts.compose import LABEL, ComposeArtifact
from scripts.docker_adapter import DockerError
from scripts.reconciliation import encode


def require(condition, code):
    if not condition:
        raise DockerError(code)


@dataclass(frozen=True)
class ProxyContract:
    subnet: str
    gateway: str
    network_id: str

    def validate(self):
        try:
            net = ipaddress.ip_network(self.subnet, strict=True)
            addr = ipaddress.ip_address(self.gateway)
            require(net.version == 4 and addr in net and addr not in (net.network_address, net.broadcast_address),
                    'proxy-ipam-invalid')
        except (ValueError, TypeError):
            raise DockerError('proxy-ipam-invalid') from None
        require(isinstance(self.network_id, str) and bool(re.fullmatch(r'[0-9a-f]{64}', self.network_id)), 'proxy-identity-required')


def validate_proxy(observation, contract):
    contract.validate()
    found = [r for r in observation.resources if r.kind == 'network' and r.name == 'portfolio-proxy']
    require(observation.complete and len(found) == 1, 'proxy-discovery-incomplete')
    r = found[0]
    f = r.facts
    require(r.identifier == contract.network_id and
            f['labels'].get(LABEL + '.managed') == 'true' and f['labels'].get(LABEL + '.role') == 'proxy' and
            f['driver'] == 'bridge' and f['scope'] == 'local' and f['internal'] is False and
            f['options'] == {} and f.get('attachable') is False and f.get('ingress') is False and
            f.get('enableIPv6') is False and
            f.get('ipam') == {'Driver': 'default', 'Options': {}, 'Config': [{'Subnet': contract.subnet, 'Gateway': contract.gateway}]},
            'proxy-ownership-or-configuration-mismatch')


@dataclass(frozen=True)
class RuntimePaths:
    state: str
    lock: str
    journal: str
    gateway_config: str
    certificate_data: str
    gateway_data: str
    certificate_budget_bytes: int

    def validate(self):
        paths = [self.state, self.lock, self.journal, self.gateway_config, self.certificate_data, self.gateway_data]
        require(type(self.certificate_budget_bytes) is int and 1048576 <= self.certificate_budget_bytes <= 10737418240,
                'operator-storage-budget-required')
        require(all(isinstance(p, str) and len(p) <= 512 for p in paths), 'operator-production-path-invalid')
        require(len(set(paths)) == len(paths), 'operator-path-collision')
        for path in paths:
            p = PurePosixPath(path)
            require(isinstance(path, str) and p.is_absolute() and str(p) == path and '..' not in p.parts and
                    len(p.parts) >= 3 and p.parts[1] not in ('tmp', 'run', 'dev', 'proc', 'sys') and
                    not path.startswith('/var/tmp/'),
                    'operator-production-path-invalid')
        from scripts.production_integration.locking import CANONICAL_LOCK_DIRECTORY
        require(self.lock == CANONICAL_LOCK_DIRECTORY, 'production-lock-path-mismatch')
        require(not any(a != b and a in PurePosixPath(b).parents for a in map(PurePosixPath, paths) for b in paths),
                'operator-path-overlap')


@dataclass(frozen=True)
class CaddyRuntime:
    image: str
    version: str
    paths: RuntimePaths

    def render(self):
        self.paths.validate()
        require(isinstance(self.image, str) and bool(re.fullmatch(
            r'(?:(?:docker.io/library/)?caddy|ghcr.io/danilfaritovich/portfolio-caddy)@sha256:[0-9a-f]{64}', self.image)),
                'caddy-pinned-image-required')
        require(isinstance(self.version, str) and bool(re.fullmatch(r'2\.[0-9]+\.[0-9]+', self.version)), 'caddy-version-required')
        owner = {LABEL + '.managed': 'true', LABEL + '.role': 'gateway'}
        # Admin is bound to container loopback by the generated Caddyfile. Only a
        # future bounded exec/reload port can reach it; no host 2019 publishing.
        model = {'name': 'portfolio-gateway', 'services': {'caddy': {
            'image': self.image, 'platform': 'linux/amd64', 'restart': 'unless-stopped',
            'read_only': True, 'cap_drop': ['ALL'], 'cap_add': ['NET_BIND_SERVICE'],
            'security_opt': ['no-new-privileges:true'], 'labels': owner,
            'command': ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'],
            'ports': ['80:80/tcp', '443:443/tcp'], 'networks': ['proxy'],
            'volumes': [{'type': 'bind', 'source': self.paths.gateway_config, 'target': '/etc/caddy', 'read_only': True,
                         'bind': {'create_host_path': False}},
                        {'type': 'bind', 'source': self.paths.certificate_data, 'target': '/data',
                         'bind': {'create_host_path': False}},
                        {'type': 'bind', 'source': self.paths.gateway_data, 'target': '/config',
                         'bind': {'create_host_path': False}}]}},
            'networks': {'proxy': {'name': 'portfolio-proxy', 'external': True}}}
        return ComposeArtifact('@gateway', 'portfolio-gateway', encode(model))
