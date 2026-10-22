"""Explicit trusted-code bindings. Nothing here deserializes authority from JSON.

Attestors are installation dependencies, never manifest fields. No attestor is
installed by CLI/CI. Stage 09.4B must review its authenticated implementation.
"""
from dataclasses import dataclass
import re

from scripts.docker_adapter import DockerError


@dataclass(frozen=True)
class SourceBinding:
    source: str
    host: str
    daemon: str
    identity: str

    def validate(self):
        if (not all(isinstance(v, str) for v in (self.source, self.host, self.daemon, self.identity)) or
            self.source not in ('runtime', 'gateway', 'readiness') or
            not re.fullmatch(r'[0-9a-f]{32}', self.host) or
            not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', self.daemon) or
            not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', self.identity)):
            raise DockerError('production-source-binding-invalid')


@dataclass(frozen=True)
class SourceAttestation:
    binding: SourceBinding
    result: object


class SourceTrust:
    def __init__(self, bindings, attestor=None):
        self.bindings = tuple(bindings)
        self.attestor = attestor
        for binding in self.bindings:
            binding.validate()
        if len(self.bindings) != 3 or {b.source for b in self.bindings} != {'runtime', 'gateway', 'readiness'}:
            raise DockerError('production-source-bindings-incomplete')

    def verify(self, name, engine_identity, port, result):
        binding = next(b for b in self.bindings if b.source == name)
        if (engine_identity != (binding.host, binding.daemon) or result.identity != binding.identity):
            raise DockerError('production-source-identity-mismatch')
        if self.attestor is None:
            raise DockerError('production-authenticated-source-attestor-unavailable')
        # The attestor must bind the exact payload, round, capture time and port
        # to its authenticated channel. Nonempty identities/verified flags alone
        # are never the authority. Raising is the only failure convention.
        proof = self.attestor.verify(binding, port, result)
        if not isinstance(proof, SourceAttestation) or proof.binding != binding or proof.result != result:
            raise DockerError('production-authenticated-source-proof-invalid')


class UnconfiguredPrivilegedExecution:
    """Separate infrastructure handoff, with no sudo/root-shell fallback."""
    def invoke(self, operation):
        raise DockerError('production-privileged-execution-not-provisioned')
