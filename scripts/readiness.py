"""Bounded readiness gates. Adapters are trusted, deadline-respecting interfaces.

No HTTP/Docker transport is installed here; Stage 07 supplies isolated transports.
HTTP adapters must disable redirects and bound each call by the supplied timeout.
"""
from dataclasses import dataclass
import math
import time
from typing import Protocol


class ReadinessContractError(Exception):
    """Fatal adapter contract failure; never convert to transient unknown evidence."""


@dataclass(frozen=True)
class ReadinessPolicy:
    deadline: float = 60
    interval: float = 2
    successes: int = 2
    probe_timeout: float = 2

    def __post_init__(self):
        values = (self.deadline, self.interval, self.probe_timeout)
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or
            not 1 <= self.deadline <= 300 or not 0.1 <= self.interval <= 30 or
            not 0.1 <= self.probe_timeout <= 10 or type(self.successes) is not int or
            not 2 <= self.successes <= 5 or self.interval * (self.successes - 1) >= self.deadline):
            raise ValueError("invalid-readiness-policy")


@dataclass(frozen=True)
class ProbeResult:
    running: bool | None
    http_status: int | None = None
    health: str = "unknown"
    redirected: bool = False


class ProbeAdapter(Protocol):
    def probe(self, component, container_id: str, *, timeout: float, follow_redirects: bool) -> ProbeResult:
        """Probe manifest HTTP port/path or actual image healthcheck of this ID.

        Do not follow redirects, exec curl/wget, publish ports, or return body/logs.
        Unknown/timeouts return unknown evidence. Container identity must be pinned.
        Raise ReadinessContractError for fatal identity/security contract violations.
        """
        ...


@dataclass(frozen=True)
class ReadinessEvidence:
    status: str  # running, ready (one success), verified, failed, unknown
    reason: str
    probes: int
    consecutive: int


def verify_readiness(component, container_id, adapter, policy=ReadinessPolicy(), *,
                     clock=time.monotonic, sleep=time.sleep):
    end = clock() + policy.deadline
    count = consecutive = 0
    status = "unknown"
    # Iteration bound also protects fake/nonadvancing clocks.
    limit = math.ceil(policy.deadline / policy.interval) + 1
    for _ in range(limit):
        remaining = end - clock()
        if remaining <= 0:
            break
        count += 1
        try:
            result = adapter.probe(component, container_id, timeout=min(policy.probe_timeout, remaining),
                                   follow_redirects=False)
        except ReadinessContractError:
            raise
        except Exception:
            result = ProbeResult(None)
        if clock() >= end:
            return ReadinessEvidence("unknown", "readiness-deadline", count, 0)
        if result.running is False:
            return ReadinessEvidence("failed", "container-not-running", count, 0)
        if result.running is not True:
            status, consecutive = "unknown", 0
        else:
            http = component["type"] == "http"
            good = ((type(result.http_status) is int and 200 <= result.http_status < 300 and
                     result.redirected is False) if http else result.health == "healthy")
            if good:
                consecutive += 1
                status = "ready"
                if consecutive >= policy.successes:
                    return ReadinessEvidence("verified", "readiness-verified", count, consecutive)
            else:
                consecutive = 0
                status = "failed" if (http and result.http_status is not None) or result.health == "unhealthy" else "running"
        sleep(min(policy.interval, max(0, end - clock())))
    return ReadinessEvidence(status, "readiness-deadline", count, consecutive)
