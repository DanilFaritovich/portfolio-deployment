"""Real VM scenarios using Stage 03 planner, Stage 05 executor and Stage 06 hooks.

This module is never invoked by pytest or make check. Offline tests use injected
boundaries and label their reports offline. No cleanup is automatic.
"""
import json
import os
from pathlib import Path
import signal
import time

from scripts.compose import LABEL, render_compose
from scripts.deployment import execute, host_lock
from scripts.deployment_journal import locked_journal
from scripts.deployment_lifecycle import DeploymentLifecycle, analyze_recovery
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import discover
from scripts.e2e.fixtures import create_registry, change_image
from scripts.e2e.observations import Reader
from scripts.e2e.safety import require
from scripts.e2e.transports import Probes, request
from scripts.readiness import ReadinessPolicy
from scripts.reconciliation import build_reconciliation_plan

SCENARIOS = (
    "initial-registration-deployment", "readiness-before-release", "route-after-readiness",
    "backend-only-update", "no-op-repeat", "failed-readiness-lkg", "stateless-rollback",
    "persistent-data", "invalid-caddy-candidate", "partial-unknown-outcome",
    "controller-crash-windows", "recovery-analysis-only", "foreign-ownership-conflict",
    "concurrent-host-lock", "unrelated-preservation",
)
POLICY = ReadinessPolicy(deadline=15, interval=1, probe_timeout=2)


class Report:
    def __init__(self, mode="real"):
        self.mode = mode
        self.performed = False
        self.results = [{"scenario": name, "status": "not-performed", "evidence": {}} for name in SCENARIOS]

    def record(self, name, status, evidence):
        require(status in ("passed", "failed", "blocked", "not-performed"), "invalid-scenario-status")
        item = next(i for i in self.results if i["scenario"] == name)
        item.update(status=status, evidence=evidence)

    @property
    def document(self):
        states = {r["status"] for r in self.results}
        status = next((s for s in ("failed", "blocked", "not-performed") if s in states), "passed")
        return {"version": 1, "mode": self.mode, "status": status,
                "realE2E": "performed" if self.mode == "real" and self.performed else "not-performed",
                "scenarios": self.results}

    def save(self, path):
        path = Path(path)
        temp = path.with_suffix(".pending")
        require(not path.is_symlink() and not temp.exists(), "unsafe-e2e-report-path")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(self.document, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)


class E2ELifecycle(DeploymentLifecycle):
    """Readiness-gated activation hook only in explicitly authorized guest runner."""
    def _gateway_verified(self):
        if self.app.spec["ingress"]:
            reader = self.provider.reader
            self.provider.refresh()
            def identities():
                return {r.facts["labels"].get("com.docker.compose.service"): r.identifier
                        for r in self.provider.sample.docker.resources if r.kind == "container" and
                        r.facts["labels"].get(f"{LABEL}.application") == self.app.id}
            pinned = identities()
            actual = reader.actual.get(self.app.id)
            require(actual is not None, "gateway-application-runtime-incomplete")
            # _verify pins IDs and rediscovery, for every actual component. This
            # includes the old image during a verified rollback.
            for c in actual.spec["components"]:
                require(self._verify(actual, c["name"]).status == "verified", "gateway-target-not-ready")
            self.provider.refresh()
            require(identities() == pinned, "gateway-readiness-identity-changed")
            reader.gateway.activate(self.desired, self.app.id, reader.endpoints, True, lease=self.journal.lease)
            self.provider.refresh()
        return super()._gateway_verified()


class Runner:
    def __init__(self, scope, adapter, images, gateway, revision):
        self.scope, self.adapter, self.images, self.gateway = scope, adapter, images, gateway
        self.directory, self.revision = scope.directory, revision
        self.registry = self.directory / "registry"
        self.probes = Probes(adapter)
        self.reader = Reader(self.registry, revision, adapter, images, gateway, self.probes)
        self.provider = self.reader.provider()
        self.report = Report()
        self.rollback_results = None
        self.crashes = []

    def deploy(self, app, *, journal=None, lifecycle_type=E2ELifecycle):
        desired, observed, _ = self.provider.refresh()
        plan = build_reconciliation_plan(desired, observed)
        app_status = next(a["status"] for a in plan.document["applications"] if a["id"] == app)
        lifecycle = None if app_status == "no-op" else lifecycle_type(
            journal or self.directory / "journal", self.probes, policy=POLICY, side_effects_excluded=True)
        result = execute(desired, observed, plan, app, self.adapter, self.provider, lifecycle=lifecycle)
        require(result, "empty-e2e-execution")
        return result

    @staticmethod
    def success(result):
        require(all(r.status == "succeeded" and r.readiness_verified and r.release_success or r.status == "no-op"
                    for r in result), "e2e-release-not-confirmed")

    def ids(self, app):
        _, _, docker = self.provider.refresh()
        return {r.facts["labels"].get("com.docker.compose.service"): r.identifier for r in docker.resources
                if r.kind == "container" and r.facts["labels"].get(f"{LABEL}.application") == app}

    def journal(self, name="journal"):
        return locked_journal(self.directory / name)

    def bootstrap(self):
        self.scope.validate()
        self.adapter.prerequisites()
        create_registry(self.registry, self.images)
        baseline = discover(self.adapter, self.images.manifests, resolve_image=self.images.evidence)
        require(baseline.complete and all(i.verified for i in baseline.images) and
                len(baseline.images) == len(self.images.manifests), "preloaded-images-required")
        require(not any(r.name.startswith(f"pd-smoke-{self.scope.session}-") or
                        r.facts["labels"].get(f"{LABEL}.test-session") == self.scope.session
                        for r in baseline.resources), "e2e-namespace-already-exists")
        # Persist baseline BEFORE any Docker mutation. Later cleanup excludes it.
        (self.directory / "baseline.json").write_text(json.dumps([[r.kind, r.identifier] for r in baseline.resources]))
        (self.directory / "baseline.json").chmod(0o600)
        self.baseline = {(r.kind, r.identifier) for r in baseline.resources}
        with host_lock():
            self.adapter._run(["network", "create", "--internal", "--driver", "bridge", "--label", f"{LABEL}.managed=true",
                "--label", f"{LABEL}.role=proxy", "--label", f"{LABEL}.test-session={self.scope.session}",
                f"pd-smoke-{self.scope.session}-proxy"])
            self.report.performed = True
            # An unrelated running container is intentionally outside deployment
            # ownership and is preserved by e2e-clean as well as the scenarios.
            self.unrelated = self.adapter._run(["create", "--pull", "never", "--network", "none", "--name",
                f"pd-e2e-{self.scope.session}-unrelated", "--label", f"{LABEL}.unrelated-fixture={self.scope.session}",
                self.images.records["v1"]]).decode().strip()
            self.adapter._run(["container", "start", self.unrelated])
        # Dedicated unit is loopback-only; package's public default unit is masked.
        from scripts.docker_adapter import run_bounded
        with host_lock():
            self.scope.validate()
            from scripts.e2e.gateway import configuration
            require(json.loads(Path("/etc/portfolio-e2e-caddy.json").read_bytes()) == configuration(),
                    "foreign-caddy-startup-configuration")
            run_bounded(["/usr/bin/systemctl", "start", "portfolio-e2e-caddy.service"], timeout=15)
        require(self.gateway.active()["apps"]["http"]["servers"]["e2e"].get("routes", []) == [],
                "caddy-not-empty-before-session")

    def scenario(self, name):
        if name == "initial-registration-deployment":
            self.success(self.deploy("e2e-sentinel"))
            self.sentinel = self.ids("e2e-sentinel")
            self.success(self.deploy("e2e-app"))
            self.initial = self.ids("e2e-app")
            code, version = request(self.reader.endpoints[("e2e-app", "backend")], 8080, "/health")
            require(code == 200 and version == b"v1", "fixture-v1-response-mismatch")
            return {"containers": len(self.initial), "sentinelContainers": len(self.sentinel)}
        if name == "readiness-before-release":
            with self.journal() as journal:
                good = [journal.last_good("e2e-app", c) for c in self.initial]
                require(all(g and g["readiness"] == "verified" and g["state"] == "succeeded" for g in good),
                        "lkg-without-readiness")
            return {"verifiedComponents": len(good), "transport": "private-ip-http-and-engine-health"}
        if name == "route-after-readiness":
            self.provider.refresh()
            require(self.provider.sample.gateway_verified, "gateway-unverified")
            require(self.provider.sample.observed.document["applications"], "missing-observation")
            routes = self.gateway.active()["apps"]["http"]["servers"]["e2e"]["routes"]
            require({r["@id"] for r in routes} == {"e2e-app", "e2e-sentinel"}, "routes-not-preserved")
            return {"activeRoutes": len(routes), "activation": "after-pinned-readiness"}
        if name == "backend-only-update":
            before = self.ids("e2e-app")
            gateway = self.gateway.active()
            change_image(self.registry, "e2e-app", self.images.records["v2"])
            self.success(self.deploy("e2e-app"))
            after = self.ids("e2e-app")
            code, version = request(self.reader.endpoints[("e2e-app", "backend")], 8080, "/health")
            require(code == 200 and version == b"v2", "fixture-v2-response-mismatch")
            require(before["backend"] != after["backend"] and before["frontend"] == after["frontend"] and
                    before["worker"] == after["worker"] and gateway == self.gateway.active(), "update-not-scoped")
            return {"changed": ["backend"], "frontendPreserved": True, "caddyPreserved": True}
        if name == "no-op-repeat":
            before, calls = self.ids("e2e-app"), len(self.adapter.up_calls)
            with self.journal() as journal:
                rows = journal.rows()
            for _ in range(2):
                result = self.deploy("e2e-app")
                require(all(r.status == "no-op" for r in result), "no-op-not-observed")
            with self.journal() as journal:
                require(journal.rows() == rows, "no-op-journal-mutated")
            require(before == self.ids("e2e-app") and calls == len(self.adapter.up_calls), "no-op-docker-mutated")
            return {"repeats": 2, "composeMutations": 0, "journalAttemptsAdded": 0}
        if name == "failed-readiness-lkg":
            with self.journal() as journal:
                self.previous_good = journal.last_good("e2e-app", "backend")
            change_image(self.registry, "e2e-app", self.images.records["bad"])
            self.rollback_results = self.deploy("e2e-app")
            with self.journal() as journal:
                require(journal.last_good("e2e-app", "backend") == self.previous_good, "failed-target-promoted")
                rows = journal.rows()
                require(any(r["target"] == self.images.records["bad"] and r["state"] == "rolled-back-verified" for r in rows),
                        "bad-readiness-not-exercised")
            return {"failedReadiness": True, "lastKnownGoodPreserved": True}
        if name == "stateless-rollback":
            require(self.rollback_results is not None and any(r.status == "rolled-back-verified" for r in self.rollback_results),
                    "stateless-rollback-unverified")
            change_image(self.registry, "e2e-app", self.images.records["v2"])
            self.provider.refresh()
            require(self.reader.actual["e2e-app"].spec == next(a for a in self.provider.sample.desired.applications
                                                          if a.id == "e2e-app").spec, "old-runtime-not-restored")
            with self.journal() as journal:
                require(not journal.incomplete(), "rollback-not-closed-atomically")
            return {"rollback": "verified", "recovery": "application-wide"}
        if name == "persistent-data":
            self.success(self.deploy("e2e-data"))
            before = self.ids("e2e-data")
            ip = self.reader.endpoints[("e2e-data", "backend")]
            status, data = request(ip, 8080, "/data")
            require(status == 200 and data, "persistent-marker-missing")
            change_image(self.registry, "e2e-data", self.images.records["data2"])
            self.success(self.deploy("e2e-data"))
            _, after = request(self.reader.endpoints[("e2e-data", "backend")], 8080, "/data")
            require(data == after and before["backend"] != self.ids("e2e-data")["backend"], "persistent-data-lost")
            return {"containerReplaced": True, "volumeMarkerPreserved": True}
        if name == "invalid-caddy-candidate":
            before = self.gateway.active()
            candidate = {"apps": {"http": {"servers": {"bad": {"listen": ["invalid-listener"]}}}}}
            try:
                self.gateway.validate(candidate)
            except DockerError:
                require(self.gateway.active() == before, "invalid-candidate-damaged-active")
                return {"candidateRejected": True, "activeConfigPreserved": True}
            raise DockerError("invalid-caddy-candidate-accepted")
        if name == "partial-unknown-outcome":
            self.success(self.deploy("e2e-unknown", journal=self.directory / "unknown-journal"))
            change_image(self.registry, "e2e-unknown", self.images.records["v2"])
            for app, fail_at, journal in (("e2e-unknown", 1, "unknown-journal"), ("e2e-partial", 2, "partial-journal")):
                original, count = self.adapter.up, [0]
                def unknown(artifact, service):
                    original(artifact, service)
                    count[0] += 1
                    if count[0] == fail_at:
                        raise DockerError("injected-unknown-after-real-up", True)
                self.adapter.up = unknown
                try:
                    result = self.deploy(app, journal=self.directory / journal)
                finally:
                    self.adapter.up = original
                require(any(r.status == "recovery-required" for r in result) and count[0] == fail_at,
                        "unknown-mutation-retried")
            require(set(self.ids("e2e-partial")) == {"backend", "frontend"}, "partial-run-not-stopped")
            return {"actualComposeBeforeInjectedUnknown": True, "partialComponents": 2, "replayed": False}
        if name == "controller-crash-windows":
            for window in ("intent", "mutation", "readiness"):
                app, journal = f"e2e-{window}", self.directory / f"crash-{window}"
                pid = os.fork()
                if pid == 0:
                    def crash():
                        os.kill(os.getpid(), signal.SIGKILL)
                    class CrashLifecycle(E2ELifecycle):
                        def prepare(self, *args):
                            super().prepare(*args)
                            if window == "intent":
                                crash()
                        def _verify(self, *args, **kwargs):
                            evidence = super()._verify(*args, **kwargs)
                            if window == "readiness" and evidence.status == "verified":
                                crash()
                            return evidence
                    if window == "mutation":
                        original = self.adapter.up
                        def after_up(*args):
                            original(*args)
                            crash()
                        self.adapter.up = after_up
                    try:
                        self.deploy(app, journal=journal, lifecycle_type=CrashLifecycle)
                    except BaseException:
                        os._exit(3)
                    os._exit(4)
                deadline = time.monotonic() + 120
                finished = False
                try:
                    while True:
                        finished, status = os.waitpid(pid, os.WNOHANG)
                        if finished:
                            break
                        if time.monotonic() >= deadline:
                            raise DockerError("crash-scenario-deadline")
                        time.sleep(0.1)
                finally:
                    if not finished:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                require(os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, "crash-window-not-reached")
                with locked_journal(journal) as j:
                    require(j.incomplete(), "crash-intent-not-durable")
                    rows = j.rows()
                    expected = {"intent": "prepared", "mutation": "applying", "readiness": "verifying"}[window]
                    require(next(r for r in rows if r["component"] == "backend")["state"] == expected,
                            "wrong-durable-crash-window")
                actual_ids = self.ids(app)
                require(set(actual_ids) == (set() if window == "intent" else {"backend"}),
                        "wrong-actual-crash-window")
                self.crashes.append(journal)
            return {"signal": "SIGKILL", "windows": ["intent", "mutation", "readiness"]}
        if name == "recovery-analysis-only":
            calls = len(self.adapter.up_calls)
            for journal in [*self.crashes, self.directory / "unknown-journal", self.directory / "partial-journal"]:
                first = analyze_recovery(journal, self.provider)
                require(first and first == analyze_recovery(journal, self.provider) and
                        all(d.decision == "recovery-required" for d in first), "recovery-replayed-or-promoted")
                with locked_journal(journal) as j:
                    require(j.incomplete(), "recovery-auto-completed")
            require(calls == len(self.adapter.up_calls), "recovery-mutated-docker")
            return {"analyzedJournals": 5, "automaticReplay": False, "automaticCompletion": False}
        if name == "foreign-ownership-conflict":
            # Create an intentionally foreign name collision; receipt identifies
            # only our fixture, never authorizes adoption by the deployment engine.
            name = f"pd-smoke-{self.scope.session}-e2e-conflict-backend-1"
            with host_lock():
                identity = self.adapter._run(["create", "--pull", "never", "--name", name, "--network", "none", "--label",
                    f"{LABEL}.foreign-fixture={self.scope.session}", self.images.records["v1"]]).decode().strip()
            receipt = self.directory / "foreign.json"
            receipt.write_text(json.dumps({"id": identity, "name": name}))
            receipt.chmod(0o600)
            calls = len(self.adapter.up_calls)
            try:
                self.deploy("e2e-conflict", journal=self.directory / "conflict-journal")
            except DockerError:
                pass
            else:
                raise DockerError("foreign-conflict-not-rejected")
            require(calls == len(self.adapter.up_calls), "foreign-resource-mutated")
            with host_lock():
                raw = self.adapter.inspect("container", identity)
                require(raw["Id"] == identity and raw["Name"] == "/" + name and
                        raw["Config"]["Labels"].get(f"{LABEL}.foreign-fixture") == self.scope.session,
                        "foreign-fixture-receipt-mismatch")
                self.adapter._run(["container", "rm", identity])  # never started, receipt + exact ID
            return {"ownershipRejected": True, "composeMutations": 0, "fixtureRemovedByReceipt": True}
        if name == "concurrent-host-lock":
            calls = len(self.adapter.up_calls)
            desired, observed, _ = self.provider.refresh()
            plan = build_reconciliation_plan(desired, observed)
            with host_lock():
                # New descriptor/lease models a simultaneous independent caller.
                result = execute(desired, observed, plan, "e2e-app", self.adapter, self.provider)
            require(all(r.status == "blocked" and r.reasons == ("host-lock-busy",) for r in result) and
                    calls == len(self.adapter.up_calls), "concurrent-deployment-not-blocked")
            return {"secondCaller": "host-lock-busy", "composeMutations": 0}
        if name == "unrelated-preservation":
            require(self.sentinel == self.ids("e2e-sentinel"), "sentinel-containers-changed")
            require(self.adapter.inspect("container", self.unrelated).get("State", {}).get("Running"),
                    "unrelated-container-not-preserved")
            fresh = discover(self.adapter, ())
            require(fresh.complete and self.baseline <= {(r.kind, r.identifier) for r in fresh.resources},
                    "baseline-resources-lost")
            return {"sentinelPreserved": True, "unrelatedRunning": True, "baselineResourcesPreserved": len(self.baseline)}
        raise ValueError("unknown scenario")

    def run(self):
        try:
            self.bootstrap()
            for name in SCENARIOS:
                try:
                    evidence = self.scenario(name)
                    self.report.record(name, "passed", evidence)
                except Exception as error:
                    self.report.record(name, "failed", {"reason": error.code if isinstance(error, DockerError) else
                                                       "scenario-assertion-or-transport-failed"})
                    break
                finally:
                    self.report.save(self.directory / "report.json")
        except (KeyboardInterrupt, SystemExit):
            pending = next((i for i in self.report.results if i["status"] == "not-performed"), None)
            if pending:
                self.report.record(pending["scenario"], "blocked", {"reason": "interrupted-no-cleanup-or-replay"})
        except Exception as error:
            self.report.record(SCENARIOS[0], "blocked", {"reason": error.code if isinstance(error, DockerError) else
                                                      "e2e-bootstrap-unavailable"})
        finally:
            self.report.save(self.directory / "report.json")
        return self.report.document
