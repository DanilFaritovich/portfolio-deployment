"""Bounded Multipass transport with deliberately allowlisted stderr evidence."""
import os
import selectors
import subprocess
import time

from scripts.docker_adapter import DockerError, MAX_OUTPUT

# Never retain arbitrary stderr lines: guest tools can print credentials, URLs,
# file contents or command arguments. Keep only these diagnostic fragments.
FRAGMENTS = ("permission denied", "access denied", "cannot open", "no such file",
             "home interface", "not connected", "connection refused", "cannot connect",
             "connection reset", "timed out", "timeout", "cloud-init", "failed",
             "not found", "does not exist", "disk space", "not running")


def safe_stderr(raw):
    text = raw[:8192].decode("utf-8", "replace").lower()
    return "; ".join(phrase for phrase in FRAGMENTS if phrase in text)[:256]


class MultipassError(DockerError):
    def __init__(self, operation, cause, unknown=False, stderr=b""):
        super().__init__(f"multipass-{operation}-{cause}", unknown)
        self.evidence = {"operation": operation, "stderr": safe_stderr(stderr)}


def run(argv, *, timeout=20, operation="client", input_data=None):
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin", "LANG": "C", "HOME": str(os.path.expanduser("~"))}
    try:
        with subprocess.Popen(argv, shell=False, env=environment,
                              stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            data, errors, total = bytearray(), bytearray(), 0
            deadline = time.monotonic() + timeout
            try:
                if input_data is not None:
                    process.stdin.write(input_data)
                    process.stdin.close()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    selector.register(process.stderr, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise MultipassError(operation, "timeout", True, errors)
                        for key, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > MAX_OUTPUT:
                                raise MultipassError(operation, "output-limit", True, errors)
                            (data if key.fileobj is process.stdout else errors).extend(chunk)
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                if process.returncode:
                    raise MultipassError(operation, "failed", True, errors)
                return bytes(data)
            except BaseException:
                process.kill()
                process.wait()
                raise
    except subprocess.TimeoutExpired:
        raise MultipassError(operation, "timeout", True) from None
    except OSError:
        raise MultipassError(operation, "unavailable", True) from None


def evidence(error):
    return {"diagnostic": error.evidence} if isinstance(error, MultipassError) else {}
