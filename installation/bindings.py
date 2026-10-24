"""Infrastructure review replaces this file in the sealed installation artifact.

No production approval issuer or host/runtime attestor has been installed. This
default is intentionally unusable. The reviewed first-phase recipe lives in
scripts.production_integration.first_phase; its external authorities remain
unconfigured. No dynamic modules or request-selected ports.
"""
from scripts.docker_adapter import DockerError


def build(installation):
    raise DockerError('production-installed-bindings-unavailable')
