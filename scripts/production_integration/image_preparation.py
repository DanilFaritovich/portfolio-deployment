"""Exact verified GHCR cache preparation under the SAME one-shot authorization.

No container/network actions, build, tag fallback, credentials or deletion. Ledger
consumption is the durable image-cache intent; unknown outcomes fence all scopes.
Caddy is intentionally excluded until independent provenance policy is approved.
"""
from dataclasses import asdict
import re
import tempfile

from scripts.docker_adapter import DockerError, run_bounded
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.artifacts import bind_engine_image
from scripts.production_integration.candidate import hashed
from scripts.production_integration.release_transport import IMAGE_REPOSITORY

SCOPE = 'prepare-project-router-image'
LIST = ['image', 'ls', '--all', '--digests', '--no-trunc', '--format', '{{json .}}']


class ImagePreparation:
    def __init__(self, *, installation, engine):
        self.installation, self.engine = installation, engine

    def preflight(self, prepared):
        c, evidence = prepared.candidate, prepared.image_evidence
        self.installation.verify()
        if (c.scope != SCOPE or evidence is None or
            not re.fullmatch(re.escape(IMAGE_REPOSITORY) + r'@sha256:[0-9a-f]{64}', c.image) or
            evidence.reference != c.image or evidence.platform != 'linux/amd64' or
            evidence.verified is not True or hashed(asdict(evidence)) != c.runtime_hash or
            self.engine.identity() != (c.host, c.daemon)):
            raise DockerError('production-image-preparation-contract-mismatch')
        return evidence

    def present(self, prepared):
        """Absence needs successful complete inventory; inspect failure is not absence."""
        c = prepared.candidate
        rows = self.engine._run(LIST).splitlines()
        if len(rows) > 512:
            raise DockerError('production-image-inventory-incomplete')
        repository, digest = c.image.split('@')
        found = []
        for raw in rows:
            try:
                row = parse_snapshot(raw)
                if (not isinstance(row, dict) or
                    not all(isinstance(row.get(k), str) for k in ('Repository', 'Digest', 'ID')) or
                    not re.fullmatch(r'sha256:[0-9a-f]{64}', row['ID'])):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise DockerError('production-image-inventory-incomplete') from None
            if row['Repository'] == repository and row['Digest'] == digest:
                found.append(row)
        if not found:
            return False
        bind_engine_image(prepared.image_evidence, self.engine.inspect('image', c.image))
        return True

    def execute(self, prepared, grant):
        grant.require()
        evidence = self.preflight(prepared)
        if grant.candidate != prepared.candidate:
            raise DockerError('production-image-preparation-grant-mismatch')
        if not self.present(prepared):
            grant.require()
            self.installation.verify()
            if self.engine.identity() != (grant.candidate.host, grant.candidate.daemon):
                raise DockerError('production-image-preparation-host-changed')
            with tempfile.TemporaryDirectory(prefix='pd-production-image-') as home:
                run_bounded(['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'image', 'pull',
                             '--platform', 'linux/amd64', evidence.reference], timeout=120, cwd=home)
        # No trust from pull stdout; rebind exact registry config/digest/platform.
        for _ in range(2):
            grant.require()
            self.preflight(prepared)
            bind_engine_image(evidence, self.engine.inspect('image', evidence.reference))
