"""Full-observation composition contract; no live provider or trust promotion.

A trusted reader must collect every source during this refresh. A journal cannot
be a source. Tokens correlate one sampling round, not cryptographic attestation.
"""
from dataclasses import dataclass
import time
import re

from scripts.docker_adapter import DockerError
from scripts.planning_inputs import load_observed_state


@dataclass(frozen=True)
class SourceEvidence:
    source: str
    round_id: str
    captured_at: float
    complete: bool
    trusted: bool


@dataclass(frozen=True)
class CompositeSample:
    desired: object
    observed: object
    docker: object
    sources: tuple[SourceEvidence, ...]
    gateway_verified: bool = False


class CompositeStateProvider:
    """reader() -> CompositeSample, including all four independently obtained sources.

    Runtime source covers effective configuration delivery; readiness source covers
    health observation scope (not release verification). Gateway verification means
    active intended routes AND required upstream/path checks passed this round.
    Stage 06 deliberately ships no real reader; fixture assertions authorize no host.
    """
    def __init__(self, reader, *, clock=time.monotonic, max_age=5):
        if type(max_age) not in (int, float) or not 0 < max_age <= 30:
            raise ValueError("invalid-observation-age")
        self.reader, self.clock, self.max_age = reader, clock, max_age
        self.sample = None

    def refresh(self):
        self.sample = None
        start = self.clock()
        sample = self.reader()
        now = self.clock()
        sources = sample.sources
        if (len(sources) != 4 or {s.source for s in sources} != {"docker", "gateway", "runtime", "readiness"} or
            len({s.round_id for s in sources}) != 1 or not all(re.fullmatch(r"[a-zA-Z0-9-]{1,64}", s.round_id) for s in sources) or
            any(s.complete is not True or s.trusted is not True or
                not start <= s.captured_at <= now or now - s.captured_at > self.max_age for s in sources)):
            raise DockerError("composite-evidence-incomplete-or-untrusted")
        observed = load_observed_state(sample.observed.document)
        scope = observed.document["scope"]
        if not scope["complete"] or not scope["trusted"] or not sample.docker.complete:
            raise DockerError("composite-evidence-incomplete-or-untrusted")
        docker = sample.docker
        if (len({i.reference for i in docker.images}) != len(docker.images) or
            len({(r.kind, r.identifier) for r in docker.resources}) != len(docker.resources) or
            len({(r.kind, r.name) for r in docker.resources}) != len(docker.resources)):
            raise DockerError("composite-conflicting-identities")
        self.sample = sample
        return sample.desired, observed, sample.docker
