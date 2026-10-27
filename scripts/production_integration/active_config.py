"""Double ACTIVE reads compared with sealed official Caddy adaptation."""
from dataclasses import replace
from contextlib import nullcontext
from pathlib import Path
import re
import tempfile
import time

from scripts.docker_adapter import DockerError, run_bounded
from scripts.gateway import route_set
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.gateway import content
from scripts.production_integration.gateway_read import GatewaySnapshot
from scripts.production_integration.admin_socket import UnixActiveChannel
from scripts.production_integration.installation import protected
from scripts.production_integration.observations import require

LIMIT = 1024 * 1024
BINARY = '/opt/portfolio-deployment/tools/caddy'


class UnconfiguredActiveChannel:
    def read_active(self, container_id, *, timeout, limit):
        raise DockerError('production-caddy-distroless-local-active-channel-unconfigured')


class InstalledConfigAdapter:
    def __init__(self, installation):
        self.installation = installation

    def adapt(self, routes):
        self.installation.verify()
        require(BINARY in dict(self.installation.files), 'production-caddy-validation-pin-required')
        protected(BINARY)
        with tempfile.TemporaryDirectory(prefix='pd-active-compare-') as directory:
            path = Path(directory) / 'Caddyfile'
            path.write_text(content(routes), encoding='utf-8')
            return run_bounded([BINARY, 'adapt', '--config', str(path), '--adapter', 'caddyfile'],
                               timeout=2, cwd=directory)


def active_json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT, 'production-active-config-response-invalid')
    try:
        value = parse_snapshot(raw)
        require(type(value) is dict, 'production-active-config-response-invalid')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise DockerError('production-active-config-response-invalid') from None


class ActiveConfigTransport:
    """Compare two independent GETs with reviewed routes officially adapted here.

    runtime.inspect() must independently verify exact container/image/version,
    effective runtime, storage and channel identity, returning GatewaySnapshot.
    routes_reader supplies approved possible routes, never proof of active state.
    The channel must GET /config/ via its fixed authenticated local transport.
    UnixActiveChannel reaches the dedicated bind mount without an image helper.
    """
    def __init__(self, engine, channel, runtime, adapter, routes_reader, *, clock=time.monotonic):
        self.engine, self.channel, self.runtime, self.adapter = engine, channel, runtime, adapter
        self.routes_reader, self.clock, self.last = routes_reader, clock, None

    def read_active(self, *, before_observation=None):
        self.last = None
        # Consider empty and admitted full route sets; do not infer from disk/LKG.
        options = ((), route_set(self.routes_reader()))
        options = tuple(dict.fromkeys(options))
        expected = [(routes, active_json(self.adapter.adapt(routes))) for routes in options]
        opening = getattr(self.runtime, 'observation', nullcontext)
        with opening():
            if before_observation is not None:
                before_observation()
            start, identity = self.clock(), self.engine.identity()
            before = self.runtime.inspect(self.channel)
            require(type(before) is GatewaySnapshot and isinstance(before.container_id, str) and
                    re.fullmatch(r'[0-9a-f]{64}', before.container_id) and before.runtime_verified is True and
                    before.ownership_verified is True and before.certificates_storage_verified is True,
                    'production-active-config-runtime-unverified')
            channel_scope = (self.channel.observation(before) if isinstance(self.channel, UnixActiveChannel) and
                             hasattr(self.runtime, 'observation') else nullcontext())
            with channel_scope:
                first = active_json(self.channel.read_active(before.container_id, timeout=1, limit=LIMIT))
                matches = [routes for routes, document in expected if document == first]
                require(len(matches) == 1, 'production-active-config-foreign-or-ambiguous')
                second = active_json(self.channel.read_active(before.container_id, timeout=1, limit=LIMIT))
            after = self.runtime.inspect(self.channel)
            require(first == second and before == after and identity == self.engine.identity() and self.clock() - start <= 5,
                    'production-active-config-changed')
            captured = self.clock()
        require(0 <= self.clock() - captured <= 5, 'production-active-config-changed')
        result = replace(before, routes=matches[0], active_caddyfile=content(matches[0]))
        self.last = (result, identity, captured)
        return result

    def verify(self, transport, image, version, sample):
        require(transport is self and self.last is not None and sample is self.last[0] and
                self.engine.identity() == self.last[1] and 0 <= self.clock() - self.last[2] <= 5 and sample.image_config_id == image.image_id and
                sample.version == version, 'production-active-config-proof-unbound')
        return sample

    def verify_current(self, sample):
        require(self.last is not None and sample is self.last[0] and
                0 <= self.clock() - self.last[2] <= 5 and self.engine.identity() == self.last[1] and
                self.runtime.inspect(self.channel) == replace(sample, routes=(), active_caddyfile=''),
                'production-active-config-evidence-expired-or-changed')
