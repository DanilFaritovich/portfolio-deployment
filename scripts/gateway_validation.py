"""Opt-in local Caddy validation adapter, isolated from pure planning.

Only generated, internally checked routes are accepted, never arbitrary Caddyfile
text. The operator supplies a trusted official Caddy binary through PATH.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

from scripts.gateway import render_caddyfile


def validate_with_caddy(routes):
    content = render_caddyfile(routes)
    binary = shutil.which("caddy")
    result = {"status": "not-performed", "version": None, "adapt": False, "validate": False}
    if not binary:
        return {**result, "reason": "caddy-not-installed"}
    if content is None:
        return {**result, "reason": "explicit-no-routes-outcome"}
    # No inherited Caddy storage, credentials, plugins or production config paths.
    with tempfile.TemporaryDirectory(prefix="gateway-validation-") as directory:
        path = Path(directory) / "candidate.Caddyfile"
        path.write_text(content, encoding="utf-8")
        env = {"PATH": os.defpath, "HOME": directory, "XDG_DATA_HOME": directory,
               "XDG_CONFIG_HOME": directory}
        try:
            # Discard command diagnostics: never expose raw subprocess messages.
            # Version output is read with a fixed cap after the process has exited.
            with tempfile.TemporaryFile() as version_output:
                version_result = subprocess.run([binary, "version"], shell=False, timeout=10,
                    cwd=directory, env=env, stdin=subprocess.DEVNULL,
                    stdout=version_output, stderr=subprocess.DEVNULL)
                version_output.seek(0)
                version = version_output.read(256).decode("ascii", errors="replace").strip()
            if version_result.returncode != 0:
                return {**result, "status": "failed", "reason": "caddy-version-failed"}
            # A version line is diagnostic data, not arbitrary subprocess output.
            match = re.match(r"v[0-9]+\.[0-9]+\.[0-9]+", version)
            result["version"] = match.group() if match else "unrecognized"
            for command in ("adapt", "validate"):
                completed = subprocess.run([binary, command, "--config", str(path), "--adapter", "caddyfile"],
                    shell=False, timeout=15, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if completed.returncode:
                    return {**result, "status": "failed", "reason": f"caddy-{command}-failed"}
                result[command] = True
        except (OSError, subprocess.TimeoutExpired):
            return {**result, "status": "failed", "reason": "caddy-unavailable-or-timeout"}
    return {**result, "status": "passed", "reason": "syntax-only-not-activation-approval"}
