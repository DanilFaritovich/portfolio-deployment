"""Fixed local empty-config validation combined with independent bootstrap trust.

The resource attestor remains mandatory for provenance, exact runtime acceptance,
IPAM/ownership, active configuration and two actual postcondition probes. Syntax
validation cannot substitute for that attestor or assert an official image origin.
"""
from pathlib import Path
import tempfile

from scripts.docker_adapter import DockerError, run_bounded
from scripts.production_integration.gateway import content
from scripts.production_integration.installation import protected

BINARY = '/opt/portfolio-deployment/tools/caddy'


class BootstrapValidationBinding:
    def __init__(self, installation, runtime, *, resources=None):
        self.installation, self.runtime, self.resources = installation, runtime, resources

    def _require(self, runtime):
        self.installation.verify()
        if runtime != self.runtime or self.resources is None:
            raise DockerError('production-bootstrap-independent-resource-attestor-required')

    def preflight(self, prepared, runtime):
        self._require(runtime)
        # External trust first: no execution of an unapproved validation binary.
        if self.resources.preflight(prepared, runtime) != prepared:
            raise DockerError('production-bootstrap-independent-resource-proof-invalid')
        self.validate_empty(runtime, content(()))
        return prepared

    def validate_empty(self, runtime, text):
        self._require(runtime)
        if text != content(()) or BINARY not in dict(self.installation.files):
            raise DockerError('production-bootstrap-empty-config-or-pin-invalid')
        protected(BINARY)
        with tempfile.TemporaryDirectory(prefix='pd-empty-caddy-') as directory:
            path = Path(directory) / 'Caddyfile'
            path.write_text(text)
            for action in ('adapt', 'validate'):
                run_bounded([BINARY, action, '--config', str(path), '--adapter', 'caddyfile'],
                            timeout=15, cwd=directory)

    def verify_created(self, prepared, runtime):
        self._require(runtime)
        return self.resources.verify_created(prepared, runtime)

    def prepare_image(self, prepared, runtime, grant):
        self._require(runtime)
        return self.resources.prepare_image(prepared, runtime, grant)
