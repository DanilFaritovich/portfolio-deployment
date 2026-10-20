"""VM-only bounded HTTP and Engine probes; no redirect, proxy, DNS or container exec."""
import base64
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

from scripts.compose import LABEL
from scripts.docker_adapter import run_bounded
from scripts.e2e.safety import require
from scripts.readiness import ProbeResult


def request(ip, port, path, *, timeout=2, method="GET", data=None, host=None):
    """Numeric destination only. http.client does not follow redirects or use env proxies."""
    require(ipaddress.ip_address(ip).version == 4 and 0.1 <= timeout <= 10 and
            type(port) is int and 1 <= port <= 65535 and path.startswith("/"), "invalid-private-http-request")
    raw = run_bounded([sys.executable, str(Path(__file__).with_name("http.py")), ip, str(port), path,
                       str(timeout), method, base64.b64encode(data).decode("ascii") if data else "", host or ""],
                      timeout=timeout)
    status, body = json.loads(raw)
    return status, base64.b64decode(body, validate=True)


def container_address(raw, adapter, application=None):
    labels = (raw.get("Config") or {}).get("Labels") or {}
    app = labels.get(f"{LABEL}.application")
    require(labels.get(f"{LABEL}.managed") == "true" and
            labels.get(f"{LABEL}.test-session") == adapter.test_session and
            isinstance(app, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", app) and
            (application is None or app == application) and
            labels.get("com.docker.compose.project") == f"pd-smoke-{adapter.test_session}-{app}",
            "probe-container-ownership-conflict")
    name = f"pd-smoke-{adapter.test_session}-{app}-private"
    attachment = (raw.get("NetworkSettings") or {}).get("Networks", {}).get(name)
    require(attachment is not None, "private-network-unavailable")
    network = adapter.inspect("network", attachment["NetworkID"])
    nlabels = network.get("Labels") or {}
    require(network.get("Id") == attachment["NetworkID"] and network.get("Name") == name and network.get("Internal") is True and
            network.get("Driver") == "bridge" and nlabels.get(f"{LABEL}.test-session") == adapter.test_session and
            nlabels.get(f"{LABEL}.application") == app and nlabels.get(f"{LABEL}.managed") == "true" and
            nlabels.get("com.docker.compose.project") == f"pd-smoke-{adapter.test_session}-{app}" and
            raw["Id"] in (network.get("Containers") or {}), "private-network-ownership-conflict")
    address = ipaddress.IPv4Address(attachment["IPAddress"])
    network_ip = network["Containers"][raw["Id"]].get("IPv4Address", "").split("/")[0]
    require(network_ip == str(address), "private-network-address-mismatch")
    require(address.is_private and not address.is_loopback and not address.is_link_local and
            any(address in ipaddress.IPv4Network(item["Subnet"]) for item in network.get("IPAM", {}).get("Config", [])
                if ":" not in item.get("Subnet", "")), "private-network-address-mismatch")
    return str(address)


class Probes:
    def __init__(self, adapter):
        self.adapter = adapter

    def probe(self, component, container_id, *, timeout, follow_redirects):
        require(follow_redirects is False and 0.1 <= timeout <= 10, "unsafe-probe-options")
        with self.adapter.budget(timeout):
            return self._probe(component, container_id, timeout=timeout)

    def _probe(self, component, container_id, *, timeout):
        started = time.monotonic()
        raw = self.adapter.inspect("container", container_id)
        require(raw.get("Id") == container_id and
                (raw.get("Config") or {}).get("Labels", {}).get("com.docker.compose.service") == component["name"],
                "probe-identity-changed")
        ip = container_address(raw, self.adapter)
        state = raw.get("State") or {}
        if not state.get("Running"):
            return ProbeResult(False)
        if component["type"] == "worker":
            require((raw.get("Config") or {}).get("Healthcheck", {}).get("Test"), "worker-healthcheck-absent")
            result = ProbeResult(True, health=state.get("Health", {}).get("Status", "unknown"))
        else:
            remaining = timeout - (time.monotonic() - started)
            if remaining < 0.1:
                return ProbeResult(None)
            status, _ = request(ip, component["port"], component["health"]["path"], timeout=remaining)
            result = ProbeResult(True, status, redirected=300 <= status < 400)
        # Probe responses from a replaced container cannot establish readiness.
        after = self.adapter.inspect("container", container_id)
        require(after.get("Id") == container_id and after.get("State", {}).get("Running") and
                container_address(after, self.adapter) == ip, "probe-identity-changed")
        return result if time.monotonic() - started < timeout else ProbeResult(None)
