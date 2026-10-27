"""Trusted-code observation budgets; never selected by requests or environment."""
import math

from scripts.docker_adapter import DockerError

DEFAULT_OBSERVATION_SECONDS = 5
ROLLOUT_OBSERVATION_SECONDS = 15


def observation_window(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30:
        raise DockerError('production-observation-window-invalid')
    return value
