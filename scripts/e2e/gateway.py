"""Test-only loopback Caddy JSON contract, distinct from production TLS renderer."""
from copy import deepcopy
from hashlib import sha256
import os
import stat
import json
from pathlib import Path
import tempfile

from scripts.docker_adapter import DockerError, run_bounded
from scripts.e2e.safety import require, private_json
from scripts.e2e.transports import request
from scripts.gateway import desired_routes
from scripts.reconciliation import encode

ADMIN, HTTP = 2019, 18080


def configuration(routes=()):
    return {"admin": {"listen": f"127.0.0.1:{ADMIN}"}, "apps": {"http": {"servers": {
        "e2e": {"listen": [f"127.0.0.1:{HTTP}"], "automatic_https": {"disable": True}, "routes": list(routes)}
    }}}}


class Gateway:
    def __init__(self, scope, *, authorization=""):
        self.scope, self.authorization = scope, authorization

    def active(self):
        self.scope.validate()
        status, raw = request("127.0.0.1", ADMIN, "/config/")
        require(status == 200, "caddy-active-config-unavailable")
        doc = json.loads(raw)
        routes = doc.get("apps", {}).get("http", {}).get("servers", {}).get("e2e", {}).get("routes", [])
        require(doc == configuration(routes) and len(routes) <= 32, "foreign-caddy-configuration")
        return doc

    def observations(self, desired, endpoints, *, updating=None):
        doc = self.active()
        active = doc["apps"]["http"]["servers"]["e2e"]["routes"]
        catalog = {r.application: r for r in desired_routes(desired)}
        result, verified = {}, True
        for entry in active:
            identity = entry.get("@id", "")
            require(identity in catalog and identity not in result, "foreign-or-duplicate-caddy-route")
            route = catalog[identity]
            dial = entry.get("handle", [{}])[0].get("upstreams", [{}])[0].get("dial")
            require(isinstance(dial, str) and entry == self.route(route, dial) and
                    dial.rsplit(":", 1)[-1] == str(route.port), "unsupported-caddy-route")
            # Host/component/port come from actual active config matched against the
            # closed registry route catalog. A stale IP is present but unverified.
            result[identity] = {"hostname": route.hostname, "component": route.component, "port": route.port}
            # Activation may replace a stale target endpoint after the caller's
            # readiness gate. Still validate its ownership/shape above; only
            # retained routes must be reachable through their current endpoint.
            if identity == updating:
                continue
            ip = endpoints.get((identity, route.component))
            if not ip or dial != f"{ip}:{route.port}":
                verified = False
                continue
            try:
                status, _ = request("127.0.0.1", HTTP, "/health", host=route.hostname)
                upstream, _ = request(ip, route.port, "/health")
                verified = verified and 200 <= status < 300 and 200 <= upstream < 300
            except DockerError:
                verified = False
        require(self.active() == doc, "caddy-changed-during-observation")
        return result, verified

    @staticmethod
    def route(route, dial):
        return {"@id": route.application, "match": [{"host": [route.hostname]}],
                "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": dial}]}], "terminal": True}

    def validate(self, candidate):
        self.scope.validate()
        with tempfile.TemporaryDirectory(prefix="pd-e2e-caddy-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(encode(candidate))
            run_bounded(["/usr/bin/caddy", "validate", "--config", str(path)], timeout=10, cwd=directory)

    def activate(self, desired, application, endpoints, ready, *, lease=None):
        """Caller holds host flock. Re-probe actual IDs before invoking this method.

        No removal, arbitrary JSON, catch-all, certificates or public listeners.
        Unknown HTTP mutation outcome is never retried or automatically restored.
        """
        require(lease is not None and lease.active is True, "gateway-host-lock-required")
        require(self.authorization == f"activate:{self.scope.session}" and ready is True,
                "explicit-ready-gateway-authorization-required")
        before = self.active()
        # Validate every active route; require reachability for retained routes.
        # The target uses the caller's readiness gate and full post-load probes.
        _, verified = self.observations(desired, endpoints, updating=application)
        require(verified, "caddy-retained-route-unreachable")
        route = next(r for r in desired_routes(desired) if r.application == application)
        require((application, route.component) in endpoints, "gateway-upstream-unavailable")
        target = self.route(route, f"{endpoints[(application, route.component)]}:{route.port}")
        candidate = deepcopy(before)
        routes = candidate["apps"]["http"]["servers"]["e2e"]["routes"]
        routes[:] = sorted([r for r in routes if r["@id"] != application] + [target], key=lambda r: r["@id"])
        if candidate == before:
            return False
        self.validate(candidate)
        directory = self.scope.directory / "gateway-snapshots"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        require(directory.resolve() == directory and stat.S_ISDIR(info.st_mode) and
                info.st_uid == os.geteuid() and not info.st_mode & 0o077, "unsafe-gateway-snapshot-directory")
        snapshot = directory / (sha256(encode(before).encode()).hexdigest() + ".json")
        if snapshot.exists():
            require(private_json(snapshot) == before, "gateway-snapshot-conflict")
        else:
            fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(encode(before))
                stream.flush()
                os.fsync(stream.fileno())
        require(self.active() == before, "caddy-candidate-stale")
        self.scope.validate()
        # A failed validation never reaches /load. Single Caddy load is atomic;
        # transport failure remains unknown and needs diagnosis, never replay.
        status, _ = request("127.0.0.1", ADMIN, "/load", method="POST", data=encode(candidate).encode())
        require(status == 200 and self.active() == candidate, "caddy-activation-unverified")
        _, verified = self.observations(desired, endpoints)
        require(verified, "caddy-route-unreachable")
        return True
