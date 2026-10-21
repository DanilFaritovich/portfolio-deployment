"""Offline checks of generated cloud-config; never initialize the host or guest."""
import shutil

import yaml

from scripts.docker_adapter import DockerError, run_bounded
from scripts.e2e.safety import require


def render(config):
    text = "#cloud-config\n" + yaml.safe_dump(config)
    validate_commands(yaml.safe_load(text))
    return text


def validate_commands(config):
    # Check the serialized representation, where YAML scalar coercion matters.
    require(isinstance(config, dict) and isinstance(config.get("runcmd"), list) and
            all(isinstance(command, list) and command and
                all(type(argument) is str for argument in command)
                for command in config["runcmd"]), "cloud-init-runcmd-arguments-must-be-strings")


def validate_file(path):
    validate_commands(yaml.safe_load(path.read_text()))
    # Use only an installed local tool. No downloads, --system or host initialization.
    binary = shutil.which("cloud-init")
    if binary is not None:
        try:
            run_bounded([binary, "schema", "--config-file", str(path)], timeout=30)
        except DockerError:
            raise DockerError("cloud-init-offline-schema-validation-failed") from None
