"""Stage 06: synthetic transports, real temporary SQLite, no daemon or sockets."""
from dataclasses import replace
import os
import sqlite3

import pytest

from scripts.composite_state import CompositeSample, CompositeStateProvider, SourceEvidence
from scripts.compose import LABEL
from scripts.deployment import execute, host_lock, LockLease
from scripts.deployment_journal import Intent, Journal, JournalError, locked_journal
from scripts.deployment_lifecycle import DeploymentLifecycle, analyze_recovery, plan_rollback, runtime_identity
from scripts.docker_adapter import DockerError
from scripts.planning_inputs import build_desired_state, load_observed_state
from scripts.readiness import ProbeResult, ReadinessEvidence, ReadinessPolicy, verify_readiness
from scripts.reconciliation import DesiredState, build_reconciliation_plan, encode
from test_deployment import FakeAdapter, SESSION, docker_state, image
from test_planning import root, put, observation, empty

SHA = "a" * 40
RUN = "1" * 32
POLICY = ReadinessPolicy(deadline=5, interval=1)


class Clock:
    value = 0

    def now(self):
        return self.value

    def sleep(self, duration):
        self.value += duration


class Probes:
    def __init__(self, results=None, fail_name=None):
        self.results = list(results or [ProbeResult(True, 204, "healthy")])
        self.calls = []
        self.fail_name = fail_name

    def probe(self, component, container_id, *, timeout, follow_redirects):
        assert follow_redirects is False and 0 < timeout <= 2
        self.calls.append((component["name"], container_id))
        if component["name"] == self.fail_name:
            return ProbeResult(True, 503, "unhealthy")
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


@pytest.mark.parametrize("kind,result,status", [
    ("http", ProbeResult(True, 200), "verified"),
    ("http", ProbeResult(True, 299), "verified"),
    ("http", ProbeResult(True, 300), "failed"),
    ("http", ProbeResult(True, 200, redirected=True), "failed"),
    ("http", ProbeResult(True, 503), "failed"),
    ("http", ProbeResult(None), "unknown"),
    ("worker", ProbeResult(True, health="healthy"), "verified"),
    ("worker", ProbeResult(True, health="unhealthy"), "failed"),
    ("worker", ProbeResult(True, health="starting"), "running"),
    ("worker", ProbeResult(None), "unknown"),
    ("worker", ProbeResult(False), "failed"),
])
def test_readiness_bounds(kind, result, status):
    clock = Clock()
    probes = Probes([result])
    e = verify_readiness({"type": kind, "name": "worker"}, "pinned-id", probes, POLICY,
                         clock=clock.now, sleep=clock.sleep)
    assert e.status == status and clock.value <= POLICY.deadline
    assert len(probes.calls) <= 5
    assert e.consecutive == 2 if status == "verified" else e.consecutive < 2


def test_consecutive_success_and_late_probe():
    clock = Clock()
    probes = Probes([ProbeResult(True, 200), ProbeResult(True, 500), ProbeResult(True, 200)])
    e = verify_readiness({"type": "http", "name": "web"}, "id", probes, POLICY, clock=clock.now, sleep=clock.sleep)
    assert e.status == "verified" and e.probes == 4
    class Slow:
        def probe(self, *args, **kwargs):
            clock.sleep(10)
            return ProbeResult(True, 200)
    assert verify_readiness({"type": "http"}, "id", Slow(), POLICY, clock=clock.now, sleep=clock.sleep).status == "unknown"


@pytest.mark.parametrize("kwargs", [{"deadline": 0}, {"deadline": float("nan")}, {"successes": 1},
                                     {"interval": 0}, {"probe_timeout": 11}, {"successes": True}])
def test_invalid_readiness_config(kwargs):
    with pytest.raises(ValueError):
        ReadinessPolicy(**kwargs)


@pytest.fixture
def worker(root):
    put(root, "internal-jobs")
    return build_desired_state(root, SHA).applications[0]


def intent(app):
    c = app.spec["components"][0]
    return Intent(c["name"], None, c["image"], runtime_identity(app, test_session=SESSION))


VERIFIED = ReadinessEvidence("verified", "readiness-verified", 2, 2)


def succeed(j, run, name):
    j.transition(run, name, "verifying", "mutation-completed")
    j.transition(run, name, "succeeded", "readiness-verified", VERIFIED)


def test_journal_transactions_and_atomic_lkg(worker, tmp_path):
    directory, lock = tmp_path / "journal", tmp_path / "lock"
    with locked_journal(directory, lock_directory=lock) as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
        with pytest.raises(JournalError):
            j.promote(RUN)
        assert j.last_good(worker.id, "worker") is None
        with pytest.raises(RuntimeError):
            with j.transaction():
                j.db.execute("UPDATE attempts SET state='applying'")
                raise RuntimeError("crash")
        assert j.rows()[0]["state"] == "prepared"
        succeed(j, RUN, "worker")
        assert j.incomplete()  # components succeeded but release not accepted
        j.promote(RUN)
        assert j.last_good(worker.id, "worker")["target"] == intent(worker).target
        assert not j.incomplete()
    with locked_journal(directory, lock_directory=lock) as j:
        assert j.last_good(worker.id, "worker")["run"] == RUN
        with pytest.raises(JournalError):
            j.prepare(RUN, worker.id, SHA, (intent(worker),))
    assert os.stat(directory / "journal.sqlite3").st_mode & 0o777 == 0o600


def test_atomic_multi_component_promotion(worker, tmp_path):
    i = intent(worker)
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (i, replace(i, component="other")))
        succeed(j, RUN, i.component)
        with pytest.raises(JournalError):
            j.promote(RUN)
        assert j.last_good(worker.id, i.component) is None
        succeed(j, RUN, "other")
        j.promote(RUN)
        assert j.last_good(worker.id, "other")["run"] == RUN


@pytest.mark.parametrize("unsafe", ["symlink", "db-symlink", "parent-symlink", "permissions", "db-permissions", "hardlink"])
def test_unsafe_journal_paths(unsafe, tmp_path):
    target = tmp_path / "journal"
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    if unsafe == "symlink":
        target.symlink_to(real, target_is_directory=True)
    elif unsafe == "parent-symlink":
        target.symlink_to(real, target_is_directory=True)
        target = target / "nested"
    else:
        target.mkdir(mode=0o700)
        if unsafe == "permissions":
            target.chmod(0o755)
        else:
            db = target / "journal.sqlite3"
            if unsafe == "db-symlink":
                db.symlink_to(real / "db")
            else:
                db.touch(mode=0o600)
                if unsafe == "db-permissions":
                    db.chmod(0o644)
                else:
                    os.link(db, real / "link")
    with pytest.raises(JournalError):
        with locked_journal(target, lock_directory=tmp_path / "l"):
            pass


@pytest.mark.parametrize("corruption", ["bytes", "version", "missing-events", "state", "good", "incomplete-schema"])
def test_corrupt_journal(worker, tmp_path, corruption):
    directory = tmp_path / "j"
    with locked_journal(directory, lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
    path = directory / "journal.sqlite3"
    if corruption == "bytes":
        path.write_bytes(b"not sqlite")
    else:
        db = sqlite3.connect(path)
        statements = {"version": "PRAGMA user_version=999", "missing-events": "DELETE FROM events",
                      "state": "UPDATE attempts SET state='succeeded'", "good": "INSERT INTO good VALUES ('internal-jobs','worker','" + RUN + "')",
                      "incomplete-schema": "DROP TABLE events"}
        db.execute(statements[corruption])
        db.commit()
        db.close()
    with pytest.raises(JournalError):
        with locked_journal(directory, lock_directory=tmp_path / "l"):
            pass


def test_lock_required_and_contention(tmp_path):
    with pytest.raises(JournalError):
        with Journal(tmp_path / "j", LockLease(False)):
            pass
    with host_lock(tmp_path / "l"):
        with pytest.raises(DockerError, match="host-lock-busy"):
            with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l"):
                pass
    assert not (tmp_path / "j").exists()


class World:
    """One synthetic coherent source round; adapters change only synthetic state."""
    def __init__(self, app, *, present=False, gateway=True):
        self.desired = DesiredState(SHA, (app,))
        self.actual = self.desired if present else DesiredState(SHA, ())
        self.app = app
        self.gateway = gateway
        self.extra_images = ()
        self.calls = 0
        self.mutate_sample = lambda sample: sample
        self.provider = CompositeStateProvider(self.read, clock=lambda: 0)

    def read(self):
        self.calls += 1
        observed = load_observed_state(observation(self.actual))
        docker = docker_state(self.actual.applications[0] if self.actual.applications else self.app,
                              bool(self.actual.applications))
        target_images = tuple(image(c["image"]) for c in self.desired.applications[0].spec["components"])
        extra = self.extra_images if self.actual == self.desired or self.actual.applications == self.desired.applications else ()
        docker = replace(docker, images=tuple({i.reference: i for i in (*docker.images, *target_images, *extra)}.values()))
        sources = tuple(SourceEvidence(s, str(self.calls), 0, True, True) for s in ("docker", "gateway", "runtime", "readiness"))
        return self.mutate_sample(CompositeSample(self.desired, observed, docker, sources, self.gateway))

    def adapter(self, error=None, rollback_error=False):
        world = self
        class Adapter(FakeAdapter):
            def up(self, artifact, service):
                self.calls.append((artifact.project, service))
                if error and len(self.calls) == 1:
                    # Simulate timeout AFTER mutation, which must not be replayed.
                    world.actual = world.desired
                    raise error
                if rollback_error and len(self.calls) > 1:
                    raise DockerError("docker-command-failed", True)
                spec = world.app.spec
                for c in spec["components"]:
                    c["image"] = artifact.model["services"][c["name"]]["image"]
                world.actual = DesiredState(SHA, (replace(world.app, spec_json=encode(spec)),))
        return Adapter()


def run(world, tmp_path, probes=None, adapter=None, **kwargs):
    _, observed, _ = world.provider.refresh()
    plan = build_reconciliation_plan(world.desired, observed)
    clock = Clock()
    lifecycle = DeploymentLifecycle(tmp_path / "j", probes or Probes(), policy=POLICY, clock=clock.now,
                                    sleep=clock.sleep, **kwargs)
    adapter = adapter or world.adapter()
    result = execute(world.desired, observed, plan, world.app.id, adapter, world.provider,
                     lock_directory=tmp_path / "l", lifecycle=lifecycle)
    return result, adapter, lifecycle


def test_success_and_verified_noop(worker, tmp_path):
    world = World(worker)
    results, adapter, lifecycle = run(world, tmp_path)
    assert len(adapter.calls) == 1 and results[0].document["releaseSuccess"]
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert j.last_good(worker.id, "worker")["run"] == lifecycle.run_id
    results, adapter, _ = run(world, tmp_path)
    assert not adapter.calls and results[0].release_success


def test_public_gateway_required(root, tmp_path):
    put(root, "project-router")
    app = build_desired_state(root, SHA).applications[0]
    world = World(app, gateway=False)
    results, _, _ = run(world, tmp_path)
    assert results[0].status == "recovery-required" and not results[0].release_success
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert j.last_good(app.id, "web") is None and j.incomplete()
    assert analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")


def test_unknown_result_never_replayed(worker, tmp_path):
    world = World(worker)
    results, adapter, _ = run(world, tmp_path, adapter=world.adapter(DockerError("docker-timeout", True)))
    assert results[0].status == "recovery-required" and len(adapter.calls) == 1
    first = analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert first[0].reason == "target-observed-verification-required"
    assert first == analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert len(adapter.calls) == 1
    with pytest.raises(JournalError, match="unfinished"):
        run(world, tmp_path)


@pytest.mark.parametrize("crash_state", ["prepared", "applying", "verifying"])
def test_crash_windows(worker, tmp_path, crash_state):
    world = World(worker, present=crash_state != "prepared")
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
        if crash_state != "prepared":
            j.transition(RUN, "worker", "applying", "mutation-started")
        if crash_state == "verifying":
            j.transition(RUN, "worker", "verifying", "mutation-completed")
    decisions = analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert decisions[0].decision == "recovery-required"
    assert decisions[0].reason == ("runtime-unverified" if crash_state == "prepared" else "target-observed-verification-required")


def test_dependencies_and_partial_release(root, tmp_path):
    def strip(spec):
        spec.pop("configRefs", None)
        spec.pop("secretRefs", None)
        for c in spec["components"]:
            c.pop("configRefs", None)
            c.pop("secretRefs", None)
    put(root, mutate=strip)
    app = build_desired_state(root, SHA).applications[0]
    world = World(app)
    probes = Probes(fail_name="backend")
    results, adapter, _ = run(world, tmp_path, probes)
    assert [c[1] for c in adapter.calls] == ["backend"]
    assert all(not r.release_success for r in results)
    assert results[1].status == results[2].status == "blocked"
    # No-op dependencies are still probed, not accepted from running/healthy flags.
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    world = World(app, present=True)
    results, adapter, _ = run(world, other, Probes(fail_name="worker"))
    assert not adapter.calls and results[0].status == "succeeded" and results[1].status == "recovery-required"
    with locked_journal(other / "j", lock_directory=other / "l") as j:
        assert j.last_good(app.id, "backend") is None


def upgraded(app):
    spec = app.spec
    spec["components"][0]["image"] = spec["components"][0]["image"].split("@")[0] + "@sha256:" + "b" * 64
    return replace(app, spec_json=encode(spec))


@pytest.mark.parametrize("failure", [None, "missing", "unverified", "effects", "rollback-failure"])
def test_stateless_rollback(worker, tmp_path, failure):
    world = World(worker)
    run(world, tmp_path)  # verified baseline
    old = worker.spec["components"][0]["image"]
    world.app = upgraded(worker)
    world.desired = DesiredState("b" * 40, (world.app,))
    if failure != "missing":
        world.extra_images = (replace(image(old), verified=failure != "unverified"),)
    class TargetFails(Probes):
        def probe(self, component, *args, **kwargs):
            return ProbeResult(True, health="unhealthy" if component["image"] != old else "healthy")
    results, adapter, _ = run(world, tmp_path, TargetFails(), adapter=world.adapter(rollback_error=failure == "rollback-failure"),
                              side_effects_excluded=failure != "effects")
    if failure is None:
        assert results[0].status == "rolled-back-verified" and len(adapter.calls) == 2
        assert world.actual.applications[0].spec["components"][0]["image"] == old
    else:
        assert results[0].status == "recovery-required"
        assert len(adapter.calls) == (2 if failure == "rollback-failure" else 1)
    assert not results[0].release_success
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert j.last_good(worker.id, "worker")["target"] == old
        if failure is None:
            assert not j.incomplete()
            recovered = [r for r in j.rows() if r["run_status"] == "recovered"]
            assert len(recovered) == 1 and recovered[0]["state"] == "recovered-verified"
        else:
            assert j.incomplete()
    if failure is None:
        # Opening the journal validates recovered history; a new deployment can
        # advance LKG without invalidating that historical restoration.
        results, _, lifecycle = run(world, tmp_path)
        assert results[0].release_success
        with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
            assert not j.incomplete()
            assert j.last_good(worker.id, "worker")["run"] == lifecycle.run_id
    else:
        with pytest.raises(JournalError, match="unfinished"):
            run(world, tmp_path)


@pytest.mark.parametrize("failure", [None, "readiness", "ownership", "incomplete", "replacement", "late-replacement", "gateway", "late-gateway", "partial", "crash"])
def test_whole_application_recovery(root, tmp_path, monkeypatch, failure):
    put(root, "project-router")
    app = build_desired_state(root, SHA).applications[0]
    spec = app.spec
    # Three independent components exercise an accepted no-op, rollback and
    # unattempted prepared intent. None may escape the final baseline checks.
    first = spec["components"][0]
    spec["components"] = [dict(first, name=n, dependsOn=[]) for n in ("alpha", "middle", "omega")]
    spec["ingress"]["component"] = "alpha"
    for c in spec["components"]:
        c["proxyNetwork"] = c["name"] == "alpha"
    app = replace(app, spec_json=encode(spec))
    world = World(app, present=True)
    accepted, _, baseline = run(world, tmp_path)
    assert all(r.release_success for r in accepted)
    old = first["image"]
    spec = app.spec
    spec["components"][1]["image"] = old.split("@")[0] + "@sha256:" + "b" * 64
    if failure == "partial":
        spec["components"][0]["image"] = old.split("@")[0] + "@sha256:" + "c" * 64
    world.app = replace(app, spec_json=encode(spec))
    world.desired = DesiredState("b" * 40, (world.app,))
    world.extra_images = (image(old),)
    class TargetFails(Probes):
        def probe(self, component, *args, **kwargs):
            unhealthy = component["name"] == "middle" and component["image"] != old
            return ProbeResult(True, 503 if unhealthy else 200)
    probes = TargetFails()
    class ScopedAdapter(FakeAdapter):
        def up(self, artifact, service):
            self.calls.append((artifact.project, service))
            actual = world.actual.applications[0]
            current = actual.spec
            next(c for c in current["components"] if c["name"] == service)["image"] = artifact.model["services"][service]["image"]
            world.actual = DesiredState(SHA, (replace(actual, spec_json=encode(current)),))
    original = DeploymentLifecycle.complete
    class Crash(BaseException):
        pass
    def finish(self, results):
        assert results[1].status == "rolled-back-verified"
        if failure == "crash":
            raise Crash()
        if failure == "readiness":
            probes.results = [ProbeResult(True, 503)]
            # TargetFails overrides the result list, so explicitly fail the
            # untouched component only in final verification.
            self.probes = Probes(fail_name="omega")
        if failure == "gateway":
            world.gateway = False
        late = False
        if failure in ("late-replacement", "late-gateway"):
            class LateProbe(Probes):
                def probe(self, component, *args, **kwargs):
                    nonlocal late
                    if component["name"] == "omega":
                        late = True
                        if failure == "late-gateway":
                            world.gateway = False
                    return ProbeResult(True, 200)
            self.probes = LateProbe()
        def corrupt(sample):
            if failure == "incomplete":
                return replace(sample, sources=sample.sources[:-1])
            if failure in ("ownership", "replacement") or (failure == "late-replacement" and late):
                resources = []
                for r in sample.docker.resources:
                    if r.kind == "container" and r.facts["labels"].get("com.docker.compose.service") == "alpha":
                        if failure == "ownership":
                            facts = r.facts
                            facts["labels"][f"{LABEL}.application"] = "foreign"
                            r = replace(r, facts_json=encode(facts))
                        else:
                            r = replace(r, identifier=r.identifier + ("-late" if late else str(world.calls)))
                    resources.append(r)
                return replace(sample, docker=replace(sample.docker, resources=tuple(resources)))
            return sample
        world.mutate_sample = corrupt
        return original(self, results)
    monkeypatch.setattr(DeploymentLifecycle, "complete", finish)
    if failure == "crash":
        with pytest.raises(Crash):
            run(world, tmp_path, probes, adapter=ScopedAdapter(), side_effects_excluded=True)
    else:
        results, _, lifecycle = run(world, tmp_path, probes, adapter=ScopedAdapter(), side_effects_excluded=True)
        assert not any(r.release_success for r in results)
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert all(j.last_good(app.id, n)["run"] == baseline.run_id for n in ("alpha", "middle", "omega"))
        if failure is None:
            assert not j.incomplete()
            rows = [r for r in j.rows() if r["run"] == lifecycle.run_id]
            assert all(r["run_status"] == "recovered" and r["state"] == "recovered-verified" for r in rows)
            assert j.db.execute("SELECT count(*) FROM events WHERE run=? AND reason='recovery-verified'", (lifecycle.run_id,)).fetchone()[0] == 3
        else:
            assert j.incomplete()
    if failure:
        monkeypatch.setattr(DeploymentLifecycle, "complete", original)
        world.mutate_sample = lambda s: s
        world.gateway = True
        with pytest.raises(JournalError, match="unfinished"):
            run(world, tmp_path)


@pytest.mark.parametrize("fault", ["event-write", "integrity", "final-integrity"])
def test_recovery_commit_is_atomic(worker, tmp_path, monkeypatch, fault):
    world = World(worker)
    _, _, baseline = run(world, tmp_path)
    old = worker.spec["components"][0]["image"]
    world.app = upgraded(worker)
    world.desired = DesiredState("b" * 40, (world.app,))
    world.extra_images = (image(old),)
    class TargetFails(Probes):
        def probe(self, component, *args, **kwargs):
            return ProbeResult(True, health="healthy" if component["image"] == old else "unhealthy")
    original = Journal.recover
    def fail(self, run_id, evidence):
        if fault == "event-write":
            self.db.execute("CREATE TEMP TRIGGER injected BEFORE INSERT ON events WHEN NEW.state='recovered-verified' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        elif fault == "final-integrity":
            self.db.execute("CREATE TEMP TRIGGER injected AFTER UPDATE ON runs WHEN NEW.status='recovered' BEGIN UPDATE events SET readiness='corrupt' WHERE run=NEW.id; END")
        else:
            self.db.execute("UPDATE events SET readiness='corrupt' WHERE run=? AND state='prepared'", (run_id,))
        return original(self, run_id, evidence)
    monkeypatch.setattr(Journal, "recover", fail)
    with pytest.raises(JournalError):
        run(world, tmp_path, TargetFails(), side_effects_excluded=True)
    # Read durable state independently, including the deliberately corrupt case.
    with sqlite3.connect(tmp_path / "j/journal.sqlite3") as db:
        assert db.execute("SELECT run FROM good").fetchone()[0] == baseline.run_id
        assert not db.execute("SELECT id FROM runs WHERE status='recovered'").fetchall()
        assert not db.execute("SELECT run FROM events WHERE state='recovered-verified'").fetchall()
    if fault != "integrity":
        with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
            assert j.incomplete()
        with pytest.raises(JournalError, match="unfinished"):
            run(world, tmp_path)
    else:
        with pytest.raises(JournalError):
            with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l"):
                pass


def test_persistent_and_changed_runtime_rollback_blocked(worker):
    old = intent(worker).target
    good = {"state": "succeeded", "readiness": "verified", "target": old, "runtime": runtime_identity(worker)}
    app = upgraded(worker)
    for storage in ({"type": "persistent", "volumes": [{"name": "data"}]}, {"type": "stateless"}):
        spec = app.spec
        spec["storage"] = storage
        if storage["type"] == "stateless":
            spec["components"][0]["dependsOn"] = ["other"]
        changed = replace(app, spec_json=encode(spec))
        docker = replace(docker_state(app, True), images=(image(old),))
        assert not plan_rollback(changed, "worker", old, good, docker, owned=True, scope_allowed=True, side_effects_excluded=True).allowed


@pytest.mark.parametrize("bad", ["incomplete", "untrusted", "missing-source", "stale", "mixed-round", "docker-incomplete", "foreign"])
def test_composite_and_foreign_observations(worker, tmp_path, bad):
    world = World(worker, present=True)
    def corrupt(sample):
        sources = sample.sources
        if bad in ("incomplete", "untrusted"):
            sources = (replace(sources[0], **{"complete" if bad == "incomplete" else "trusted": False}), *sources[1:])
        if bad == "missing-source":
            sources = sources[:-1]
        if bad == "stale":
            sources = (replace(sources[0], captured_at=-1), *sources[1:])
        if bad == "mixed-round":
            sources = (replace(sources[0], round_id="other"), *sources[1:])
        doc = sample.observed.document
        if bad == "foreign":
            doc["applications"][0]["owner"]["manager"] = "foreign"
        return replace(sample, sources=sources, observed=load_observed_state(doc),
                       docker=replace(sample.docker, complete=bad != "docker-incomplete"))
    world.mutate_sample = corrupt
    if bad == "foreign":
        results, adapter, _ = run(world, tmp_path)
        assert all(r.status == "blocked" for r in results) and not adapter.calls
    else:
        with pytest.raises(DockerError):
            world.provider.refresh()
        assert world.provider.sample is None
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
    decisions = analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert decisions[0].reason in ("fresh-state-unavailable", "runtime-unverified")


@pytest.mark.parametrize("after_mutation", [False, True])
def test_executor_crash_keeps_durable_intent(worker, tmp_path, after_mutation):
    world = World(worker)
    class Crash(BaseException):
        pass
    class Crashing(FakeAdapter):
        def up(self, artifact, service):
            # Read from another SQLite connection: intent must already be committed.
            db = sqlite3.connect(tmp_path / "j/journal.sqlite3")
            assert db.execute("SELECT state FROM attempts").fetchone()[0] == "applying"
            db.close()
            if after_mutation:
                world.actual = world.desired
            raise Crash()
    with pytest.raises(Crash):
        run(world, tmp_path, adapter=Crashing())
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert j.rows()[0]["state"] == "applying" and j.last_good(worker.id, "worker") is None
    result = analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert result[0].reason == ("target-observed-verification-required" if after_mutation else "runtime-unverified")


def test_journal_write_failure_prevents_mutation(worker, tmp_path, monkeypatch):
    world = World(worker)
    adapter = world.adapter()
    original = Journal.transition
    def fail(self, run, component, state, *args, **kwargs):
        if state == "applying":
            raise JournalError("journal-write-failed")
        return original(self, run, component, state, *args, **kwargs)
    monkeypatch.setattr(Journal, "transition", fail)
    with pytest.raises(JournalError):
        run(world, tmp_path, adapter=adapter)
    assert not adapter.calls


def test_lkg_survives_failed_atomic_promotion(worker, tmp_path):
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
        succeed(j, RUN, "worker")
        j.promote(RUN)
        second = "2" * 32
        j.prepare(second, worker.id, "b" * 40, (intent(upgraded(worker)),))
        succeed(j, second, "worker")
        j.db.execute("CREATE TEMP TRIGGER injected BEFORE UPDATE ON good BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with pytest.raises(JournalError):
            j.promote(second)
        assert j.last_good(worker.id, "worker")["run"] == RUN
        assert next(r for r in j.rows() if r["run"] == second)["run_status"] == "active"


def test_replacement_during_readiness_is_not_verified(worker, tmp_path):
    world = World(worker)
    calls = 0
    def replaced(sample):
        nonlocal calls
        calls += 1
        if calls >= 5:
            resources = tuple(replace(r, identifier=r.identifier + "-replacement") if r.kind == "container" else r
                              for r in sample.docker.resources)
            return replace(sample, docker=replace(sample.docker, resources=resources))
        return sample
    world.mutate_sample = replaced
    result, _, _ = run(world, tmp_path)
    assert result[0].status == "recovery-required" and not result[0].readiness_verified


def test_scope_and_policy_are_required_for_rollback(worker):
    old = worker.spec["components"][0]["image"]
    app = upgraded(worker)
    good = {"state": "succeeded", "readiness": "verified", "target": old,
            "runtime": runtime_identity(worker, test_session=SESSION)}
    docker = replace(docker_state(app, True), images=(image(old),))
    args = dict(owned=True, scope_allowed=True, side_effects_excluded=True, test_session=SESSION)
    assert plan_rollback(app, "worker", old, good, docker, **args).allowed
    for field, value in (("owned", False), ("scope_allowed", False), ("side_effects_excluded", False),
                         ("test_session", "f" * 16)):
        assert not plan_rollback(app, "worker", old, good, docker, **{**args, field: value}).allowed


def test_dependency_success_and_public_verification(root, tmp_path):
    def strip(spec):
        spec.pop("configRefs", None)
        spec.pop("secretRefs", None)
        for c in spec["components"]:
            c.pop("configRefs", None)
            c.pop("secretRefs", None)
    put(root, mutate=strip)
    app = build_desired_state(root, SHA).applications[0]
    world = World(app, present=True)
    probes = Probes()
    results, adapter, _ = run(world, tmp_path, probes)
    assert all(r.release_success for r in results) and not adapter.calls
    names = [c[0] for c in probes.calls]
    assert names.index("backend") < names.index("frontend")


def test_source_flags_never_promote_snapshot(worker):
    world = World(worker)
    def incomplete(sample):
        doc = sample.observed.document
        doc["scope"]["complete"] = False
        return replace(sample, observed=load_observed_state(doc))
    world.mutate_sample = incomplete
    with pytest.raises(DockerError, match="incomplete-or-untrusted"):
        world.provider.refresh()


def test_probe_timeout_and_single_success_are_not_verified():
    clock = Clock()
    class Timeout:
        def probe(self, *args, **kwargs):
            raise TimeoutError("must never be journaled")
    component = {"type": "http", "name": "web", "port": 8080,
                 "health": {"strategy": "http", "path": "/health"}}
    e = verify_readiness(component, "id", Timeout(), POLICY, clock=clock.now, sleep=clock.sleep)
    assert e.status == "unknown" and e.probes == 5
    clock = Clock()
    probes = Probes([ProbeResult(None)] * 4 + [ProbeResult(True, 200)])
    e = verify_readiness(component, "id", probes, POLICY, clock=clock.now, sleep=clock.sleep)
    assert e.status == "ready" and e.consecutive == 1


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_journal_sidecar_symlinks_rejected(tmp_path, suffix):
    directory = tmp_path / "j"
    directory.mkdir(mode=0o700)
    (directory / ("journal.sqlite3" + suffix)).symlink_to(tmp_path / "victim")
    with pytest.raises(JournalError, match="unsafe-journal-file"):
        with locked_journal(directory, lock_directory=tmp_path / "l"):
            pass
    assert not (tmp_path / "victim").exists()


@pytest.mark.parametrize("sql", ["UPDATE attempts SET target='secret-value'",
                                 "UPDATE attempts SET updated='invalid-time'",
                                 "UPDATE events SET readiness='corrupted'",
                                 "UPDATE runs SET reason='secret-value'"])
def test_invalid_journal_values_are_rejected(worker, tmp_path, sql):
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
        j.db.execute(sql)
    with pytest.raises(JournalError) as error:
        with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l"):
            pass
    assert "secret-value" not in str(error.value)


def test_untrusted_post_mutation_cannot_promote(worker, tmp_path):
    world = World(worker)
    def lose_evidence(sample):
        if world.actual.applications:
            return replace(sample, sources=(replace(sample.sources[0], trusted=False), *sample.sources[1:]))
        return sample
    world.mutate_sample = lose_evidence
    results, adapter, _ = run(world, tmp_path)
    assert len(adapter.calls) == 1 and results[0].status == "recovery-required"
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        assert j.last_good(worker.id, "worker") is None


def test_private_lock_file_permissions(tmp_path):
    directory = tmp_path / "l"
    with host_lock(directory):
        pass
    (directory / "host.lock").chmod(0o644)
    with pytest.raises(DockerError, match="unsafe-lock-file"):
        with host_lock(directory):
            pass


def test_recovery_of_removed_component_is_blocked(root, tmp_path):
    def strip(spec):
        spec.pop("configRefs", None)
        spec.pop("secretRefs", None)
        for c in spec["components"]:
            c.pop("configRefs", None)
            c.pop("secretRefs", None)
    put(root, mutate=strip)
    app = build_desired_state(root, SHA).applications[0]
    world = World(app, present=True)
    old_worker = next(c for c in app.spec["components"] if c["name"] == "worker")
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, app.id, SHA, (Intent("worker", None, old_worker["image"], runtime_identity(app, test_session=SESSION)),))
    spec = app.spec
    spec["components"] = [c for c in spec["components"] if c["name"] != "worker"]
    world.desired = DesiredState(SHA, (replace(app, spec_json=encode(spec)),))
    decisions = analyze_recovery(tmp_path / "j", world.provider, lock_directory=tmp_path / "l")
    assert decisions[0].reason == "runtime-unverified"


def test_missing_lkg_pointer_is_incomplete_journal(worker, tmp_path):
    with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l") as j:
        j.prepare(RUN, worker.id, SHA, (intent(worker),))
        succeed(j, RUN, "worker")
        j.promote(RUN)
        j.db.execute("DELETE FROM good")
    with pytest.raises(JournalError, match="incomplete-last-known-good"):
        with locked_journal(tmp_path / "j", lock_directory=tmp_path / "l"):
            pass
