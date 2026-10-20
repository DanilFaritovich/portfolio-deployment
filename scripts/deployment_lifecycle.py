"""Stage 06 hooks for the Stage 05 executor; no new live execution entry point."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import re
import uuid

from scripts.compose import LABEL, digest, labels, render_compose
from scripts.composite_state import CompositeStateProvider
from scripts.deployment import ExecutionResult, preflight_resources
from scripts.deployment_journal import Intent, Journal, JournalError, locked_journal
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import container_runtime, owned_resource
from scripts.readiness import ReadinessPolicy, verify_readiness
from scripts.reconciliation import build_reconciliation_plan, encode


def runtime_identity(app, *, test_session=None):
    """Nonsecret identity covers whole app, storage, mounts, networks and refs.

    Reference contents are not established by hashes of IDs; Stage 05 still blocks
    every runtime reference. No secret material is ever hashed or journaled here.
    """
    spec = app.spec
    for c in spec["components"]:
        c.pop("image")
    return digest({"spec": spec, "project": render_compose(app, test_session=test_session).project})


@dataclass(frozen=True)
class RollbackDecision:
    allowed: bool
    reason: str


def plan_rollback(app, component, previous, good, docker, *, owned, scope_allowed, side_effects_excluded, test_session=None):
    spec = app.spec
    c = next(c for c in spec["components"] if c["name"] == component)
    old = next((i for i in docker.images if i.reference == previous), None)
    safe = (spec["storage"]["type"] == "stateless" and not any(x["volumeMounts"] for x in spec["components"]) and
            not spec["configRefs"] and not spec["secretRefs"] and
            not any(x["configRefs"] or x["secretRefs"] for x in spec["components"]) and
            previous and previous != c["image"] and good and good["state"] == "succeeded" and
            good["readiness"] == "verified" and good["target"] == previous and
            good["runtime"] == runtime_identity(app, test_session=test_session) and old and old.verified and
            old.platform == c["platform"] and docker.complete and owned is True and
            scope_allowed is True and side_effects_excluded is True)
    return RollbackDecision(bool(safe), "rollback-started" if safe else "rollback-unsafe")


class DeploymentLifecycle:
    """Trusted offline API. Production authorization/runtime transports are absent.

    side_effects_excluded is an explicit trusted controller-policy assertion, never
    derived from manifest omission. Default disallows rollback even for stateless.
    One instance is one attempt; run IDs cannot be replayed to repeat mutations.
    """
    def __init__(self, directory, probes, *, run_id=None, policy=ReadinessPolicy(),
                 side_effects_excluded=False, clock=None, sleep=None):
        self.directory, self.probes = directory, probes
        self.run_id = run_id or uuid.uuid4().hex
        self.policy = policy
        self.side_effects_excluded = side_effects_excluded
        self.timing = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v is not None}
        self.verified = set()
        self.journal = None

    @contextmanager
    def open(self, lease, adapter, provider):
        # Stage 06 does not expand Stage 05's real operations boundary.
        if not re.fullmatch(r"[0-9a-f]{16}", adapter.test_session or "") or not isinstance(provider, CompositeStateProvider):
            raise DockerError("lifecycle-offline-scope-required")
        self.adapter, self.provider = adapter, provider
        with Journal(self.directory, lease) as journal:
            self.journal = journal
            try:
                yield self
            finally:
                self.journal = None

    def prepare(self, desired, app, operations, plan):
        self.desired, self.app = desired, app
        previous = {}
        for o in operations:
            before = next((e["before"] for e in plan.document["entries"] if e["application"] == app.id and
                           e["component"] == o.component and e["action"] == "update-component"), None)
            previous[o.component] = before.get("image") if before else (o.image if o.action == "no-op" else None)
        self.previous = previous
        self.journal.prepare(self.run_id, app.id, desired.git_revision,
                             tuple(Intent(o.component, previous[o.component], o.image, runtime_identity(app, test_session=self.adapter.test_session)) for o in operations))

    def dependencies_ready(self, component):
        if not set(component["dependsOn"]) <= self.verified:
            return False
        try:
            return all(self._verify(self.app, name).status == "verified" for name in component["dependsOn"])
        except Exception:
            return False

    def block(self, component, code):
        self.journal.transition(self.run_id, component, "recovery-required", code)

    def _transition(self, name, state, reason, evidence=None):
        self.journal.transition(self.run_id, name, state, reason, evidence)

    def _fresh_target(self, app, name):
        d, observed, docker = self.provider.refresh()
        if d != self.desired:
            raise DockerError("desired-revision-or-content-changed")
        artifact = render_compose(app, test_session=self.adapter.test_session)
        if preflight_resources(app, artifact, docker):
            raise DockerError("runtime-unverified")
        target_desired = replace(d, applications=tuple(app if a.id == app.id else a for a in d.applications))
        plan = build_reconciliation_plan(target_desired, observed)
        # Configuration comparison reuses Stage 03. Health is checked below.
        entries = plan.document["entries"]
        if any(e["status"] == "blocked" for e in entries):
            raise DockerError("runtime-unverified")
        matching = [e for e in entries if e["application"] == app.id and e["component"] == name]
        if not matching or any(e["action"] not in ("no-op", "health-observation") for e in matching):
            raise DockerError("runtime-unverified")
        found = [r for r in docker.resources if r.kind == "container" and
                 r.facts["labels"].get("com.docker.compose.project") == artifact.project and
                 r.facts["labels"].get("com.docker.compose.service") == name]
        c = next(c for c in app.spec["components"] if c["name"] == name)
        images = [i for i in docker.images if i.reference == c["image"]]
        if (len(found) != 1 or len(images) != 1 or
            not owned_resource(found[0], labels(app.id, name), artifact.project, name) or
            found[0].facts["state"].get("Status") != "running" or
            found[0].facts["labels"].get(f"{LABEL}.runtime") != digest(c) or
            not container_runtime(found[0], artifact, name, images[0])):
            raise DockerError("runtime-unverified")
        return found[0], docker

    def _verify(self, app, name):
        resource, _ = self._fresh_target(app, name)
        component = next(c for c in app.spec["components"] if c["name"] == name)
        evidence = verify_readiness(component, resource.identifier, self.probes, self.policy, **self.timing)
        # Fresh rediscovery closes the replacement/configuration race during probes.
        after, _ = self._fresh_target(app, name)
        if after.identifier != resource.identifier:
            raise DockerError("runtime-unverified")
        return evidence

    def _gateway_verified(self):
        if self.app.spec["ingress"] is None:
            return True
        sample = self.provider.sample
        app = next((a for a in sample.observed.document["applications"] if a["id"] == self.app.id), None)
        return (sample.gateway_verified is True and app is not None and
                app["route"]["existence"] == "present" and app["route"]["configuration"] == self.app.spec["ingress"])

    def _before_mutation(self, operation, artifact):
        d, _, docker = self.provider.refresh()
        if d != self.desired or preflight_resources(self.app, artifact, docker):
            raise DockerError("runtime-unverified")
        self.adapter.authorize(artifact)
        found = [r for r in docker.resources if r.kind == "container" and
                 r.facts["labels"].get("com.docker.compose.project") == artifact.project and
                 r.facts["labels"].get("com.docker.compose.service") == operation.component]
        if operation.action == "create":
            if found:
                raise DockerError("runtime-unverified")
        else:
            spec = self.app.spec
            next(c for c in spec["components"] if c["name"] == operation.component)["image"] = self.previous[operation.component]
            self._fresh_target(replace(self.app, spec_json=encode(spec)), operation.component)

    def step(self, operation, artifact):
        name = operation.component
        if operation.action != "no-op":
            try:
                self._before_mutation(operation, artifact)
            except Exception:
                self._transition(name, "recovery-required", "runtime-unverified")
                return ExecutionResult(operation, "recovery-required", ("runtime-unverified",))
            self._transition(name, "applying", "mutation-started")  # durable BEFORE adapter.up
            try:
                self.adapter.up(artifact, name)
            except Exception as error:
                unknown = not isinstance(error, DockerError) or error.unknown
                self._transition(name, "outcome-unknown" if unknown else "failed",
                                 "mutation-outcome-unknown" if unknown else "mutation-failed")
                # Always rediscover, even after timeout. Never repeat an unknown mutation.
                try:
                    self.provider.refresh()
                except Exception:
                    pass
                if unknown:
                    self._transition(name, "recovery-required", "recovery-analysis-required")
                    return ExecutionResult(operation, "recovery-required", ("mutation-outcome-unknown",))
                return self._rollback(operation)
        self._transition(name, "verifying", "mutation-completed")
        try:
            evidence = self._verify(self.app, name)
        except Exception:
            self._transition(name, "recovery-required", "fresh-state-unavailable")
            return ExecutionResult(operation, "recovery-required", ("fresh-state-unavailable",))
        if evidence.status != "verified":
            self._transition(name, "failed", "readiness-failed", evidence)
            return self._rollback(operation)
        self._transition(name, "succeeded", "readiness-verified", evidence)
        self.verified.add(name)
        return ExecutionResult(operation, "succeeded", ("readiness-verified",), readiness_verified=True)

    def _rollback(self, operation):
        name = operation.component
        try:
            # Ownership/runtime must be corroborated for the failed TARGET before rollback.
            _, docker = self._fresh_target(self.app, name)
            decision = plan_rollback(self.app, name, self.previous[name], self.journal.last_good(self.app.id, name),
                                     docker, owned=True, scope_allowed=True,
                                     side_effects_excluded=self.side_effects_excluded, test_session=self.adapter.test_session)
            if not decision.allowed:
                self._transition(name, "recovery-required", "rollback-unsafe")
                return ExecutionResult(operation, "recovery-required", ("rollback-unsafe",))
            spec = self.app.spec
            next(c for c in spec["components"] if c["name"] == name)["image"] = self.previous[name]
            old_app = replace(self.app, spec_json=encode(spec))
            artifact = render_compose(old_app, test_session=self.adapter.test_session)
            self.adapter.authorize(artifact)
            if preflight_resources(old_app, artifact, docker):
                raise DockerError("rollback-unsafe")
            self._transition(name, "rolling-back", "rollback-started")
            self.adapter.up(artifact, name)
            evidence = self._verify(old_app, name)
            if evidence.status != "verified" or not self._gateway_verified():
                raise DockerError("rollback-failed")
            self._transition(name, "rolled-back-verified", "rollback-verified", evidence)
            return ExecutionResult(operation, "rolled-back-verified", ("rollback-verified",), readiness_verified=True)
        except JournalError:
            raise
        except Exception:
            try:
                self.provider.refresh()
            except Exception:
                pass
            self._transition(name, "recovery-required", "rollback-failed")
            return ExecutionResult(operation, "recovery-required", ("rollback-failed",))

    def complete(self, results):
        if all(r.status == "succeeded" for r in results):
            try:
                # Reverify all components after the sequential rollout, including dependencies.
                for name in sorted(self.verified):
                    evidence = self._verify(self.app, name)
                    if evidence.status != "verified":
                        raise DockerError("readiness-failed")
                if not self._gateway_verified():
                    raise DockerError("gateway-unverified")
                self.journal.promote(self.run_id)
                return [replace(r, release_success=True) for r in results]
            except JournalError:
                raise
            except Exception as error:
                # Component readiness alone must never become a public release baseline.
                code = error.code if isinstance(error, DockerError) and error.code in ("gateway-unverified", "readiness-failed") else "fresh-state-unavailable"
                self.journal.fail_run(self.run_id, code)
                return [replace(r, status="recovery-required", reasons=("release-verification-incomplete",)) for r in results]
        self.journal.fail_run(self.run_id)
        return results


@dataclass(frozen=True)
class RecoveryDecision:
    run: str
    component: str
    decision: str
    reason: str


def analyze_recovery(directory, provider, *, lock_directory="/tmp/portfolio-deployment-locks"):
    """Read-only decisions under lock; no replay, promotion or automatic mutation.

    Even a matching target needs a NEW verification and separately authorized
    recovery procedure. Repeated analysis of the same evidence is deterministic.
    """
    with locked_journal(directory, lock_directory=lock_directory) as journal:
        rows = journal.incomplete()
        try:
            if not isinstance(provider, CompositeStateProvider):
                raise DockerError("composite-provider-required")
            desired, observed, docker = provider.refresh()
        except Exception:
            return tuple(RecoveryDecision(r["run"], r["component"], "recovery-required", "fresh-state-unavailable") for r in rows)
        decisions = []
        for r in rows:
            app = next((a for a in desired.applications if a.id == r["application"]), None)
            current = next((a for a in observed.document["applications"] if a["id"] == r["application"]), None)
            c = next((c for c in current["components"] if c["name"] == r["component"]), None) if current else None
            good = journal.last_good(r["application"], r["component"])
            image = c["imageIdentity"] if c else None
            owned = (current and current["owner"] == {"manager": "portfolio-deployment", "application": r["application"]} and
                     c and c["owner"] == {"manager": "portfolio-deployment", "application": r["application"], "component": r["component"]})
            reason = "runtime-unverified"
            if (app and any(x["name"] == r["component"] for x in app.spec["components"]) and
                desired.git_revision == r["revision"] and owned and image and image["relationship"] == "verified" and c["runtimeComplete"]):
                # Stage 03 claims must also agree with actual owned Docker inventory.
                artifact = render_compose(app)
                resources = [x for x in docker.resources if x.kind == "container" and
                             owned_resource(x, labels(app.id, r["component"]))]
                if len(resources) == 1:
                    ref = image["pullReference"]
                    spec = app.spec
                    next(x for x in spec["components"] if x["name"] == r["component"])["image"] = ref
                    actual_app = replace(app, spec_json=encode(spec))
                    # Derive smoke scope only from trusted actual project identity.
                    project = resources[0].facts["labels"].get("com.docker.compose.project", "")
                    match = re.fullmatch(r"pd-smoke-([0-9a-f]{16})-" + re.escape(app.id), project)
                    artifact = render_compose(actual_app, test_session=match[1] if match else None)
                    ims = [i for i in docker.images if i.reference == ref and i.verified]
                    if (runtime_identity(app, test_session=match[1] if match else None) == r["runtime"] and
                        len(ims) == 1 and owned_resource(resources[0], labels(app.id, r["component"]), artifact.project, r["component"]) and
                        not preflight_resources(actual_app, artifact, docker) and
                        resources[0].facts["labels"].get(f"{LABEL}.runtime") == digest(next(x for x in actual_app.spec["components"] if x["name"] == r["component"])) and
                        container_runtime(resources[0], artifact, r["component"], ims[0]) and
                        all(e["action"] in ("no-op", "health-observation") for e in
                            build_reconciliation_plan(replace(desired, applications=tuple(actual_app if a.id == actual_app.id else a for a in desired.applications)), observed).document["entries"]
                            if e["application"] == actual_app.id and e["component"] == r["component"])):
                        if ref == r["target"]:
                            reason = "target-observed-verification-required"
                        elif good and ref == good["target"] and good["runtime"] == r["runtime"]:
                            reason = "previous-observed-verification-required"
            decisions.append(RecoveryDecision(r["run"], r["component"], "recovery-required", reason))
        return tuple(decisions)
