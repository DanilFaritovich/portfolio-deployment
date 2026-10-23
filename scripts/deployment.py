"""Serialized, scoped execution. Stage 03 is a diff, never authorization."""

from dataclasses import dataclass
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import stat

from scripts.compose import LABEL, digest, labels, render_compose, startup_order
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import container_runtime, owned_resource
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan, encode
from scripts.validate_registry import RegistryValidationError


@dataclass(frozen=True)
class ExecutionOperation:
    application: str
    component: str
    git_revision: str
    image: str
    action: str
    eligibility: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    operation: ExecutionOperation
    status: str
    reasons: tuple[str, ...]
    resources: tuple[str, ...] = ()
    next_checks: tuple[str, ...] = ()
    readiness_verified: bool = False
    release_success: bool = False

    @property
    def document(self):
        o = self.operation
        return {"application": o.application, "component": o.component, "gitRevision": o.git_revision,
                "image": o.image, "action": o.action, "status": self.status, "reasons": list(self.reasons),
                "resources": list(self.resources), "requiredNextChecks": list(self.next_checks),
                "readinessVerified": self.readiness_verified, "releaseSuccess": self.release_success}


@dataclass
class LockLease:
    active: bool = True
    fd: int | None = None
    path: str | None = None


_ACTIVE_LEASES = {}


def owns_host_lock(lease):
    record = _ACTIVE_LEASES.get(id(lease))
    if record is None or record != (lease, lease.fd, lease.path) or lease.active is not True:
        return False
    try:
        actual, expected = os.fstat(lease.fd), os.lstat(lease.path)
        return (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
    except (OSError, TypeError):
        return False


@contextmanager
def host_lock(directory="/tmp/portfolio-deployment-locks"):
    """One host-wide fail-fast flock for all endpoints/runners using this path.

    Fixed path (not caller-selected per app/endpoint) is used by live execution.
    Never unlink: a second inode would split serialization. Tests inject a directory.
    """
    path = Path(directory)
    fd = None
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise DockerError("unsafe-lock-directory")
        fd = os.open(path / "host.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
            raise DockerError("unsafe-lock-file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DockerError("host-lock-busy") from None
    except (OSError, DockerError) as error:
        if fd is not None:
            os.close(fd)
        if isinstance(error, DockerError):
            raise
        raise DockerError("host-lock-unavailable") from None
    lease = LockLease(fd=fd, path=str(path / 'host.lock'))
    _ACTIVE_LEASES[id(lease)] = (lease, fd, lease.path)
    try:
        yield lease
    finally:
        _ACTIVE_LEASES.pop(id(lease), None)
        lease.active = False
        os.close(fd)


def preflight_resources(application, artifact, observation):
    """Return fixed blockers before any mutation; all declared resources are checked."""
    if not observation.complete:
        return ("docker-discovery-incomplete",)
    reasons = set()
    spec, model = application.spec, artifact.model
    if spec["configRefs"] or spec["secretRefs"] or any(c["configRefs"] or c["secretRefs"] for c in spec["components"]):
        reasons.add("runtime-reference-contract-unavailable")
    if not spec["enabled"]:
        reasons.add("application-disabled")
    images = {i.reference: i for i in observation.images}
    for c in spec["components"]:
        image = images.get(c["image"])
        if c["platform"] != "linux/amd64":
            reasons.add("unsupported-platform")
        if not image:
            reasons.add("immutable-image-unavailable")
        elif not image.verified or image.platform != c["platform"]:
            reasons.add("oci-mapping-unverified")
        elif json.loads(image.defaults_json).get("Volumes"):
            declared = {m["mountPath"] for m in c["volumeMounts"]}
            if not set(json.loads(image.defaults_json)["Volumes"]) <= declared:
                reasons.add("image-anonymous-volume-forbidden")
    for c in spec["components"]:
        if "runtimeSecurity" in c:
            image = images.get(c["image"])
            health = json.loads(image.defaults_json).get("Healthcheck") if image else None
            if not isinstance(health, dict) or not health.get("Test") or health["Test"][0] == "NONE":
                reasons.add("required-image-healthcheck-unavailable")
            if any(m["mountPath"] == "/tmp" for m in c["volumeMounts"]) and c["runtimeSecurity"]["tmpfs"]:
                reasons.add("runtime-tmpfs-volume-overlap")
    for r in observation.resources:
        if r.kind in ("network", "volume") and r.facts["labels"].get("com.docker.compose.project") == artifact.project:
            section = "networks" if r.kind == "network" else "volumes"
            key = r.facts["labels"].get(f"com.docker.compose.{r.kind}")
            expected = model.get(section, {}).get(key)
            if not expected or expected["name"] != r.name or not owned_resource(r, expected.get("labels", {}), artifact.project, key):
                reasons.add("project-foreign-resource")
    for kind, section in (("network", "networks"), ("volume", "volumes")):
        for key, expected in model.get(section, {}).items():
            found = [r for r in observation.resources if r.kind == kind and r.name == expected["name"]]
            if len(found) > 1:
                reasons.add("resource-identity-conflict")
            if expected.get("external"):
                required = {f"{LABEL}.managed": "true", f"{LABEL}.role": "proxy"}
                session = model["services"][next(iter(model["services"]))]["labels"].get(f"{LABEL}.test-session")
                if session:
                    required[f"{LABEL}.test-session"] = session
                if not found or not owned_resource(found[0], required) or found[0].facts["driver"] != "bridge":
                    reasons.add("required-proxy-network-unavailable-or-foreign")
            elif found:
                r = found[0]
                if not owned_resource(r, expected["labels"], artifact.project, key):
                    reasons.add("resource-ownership-conflict")
                if (r.facts["driver"] != ("bridge" if kind == "network" else "local") or
                    r.facts["scope"] != "local" or r.facts["options"]):
                    reasons.add("unsupported-resource-configuration")
                if kind == "network" and r.facts["internal"] is not True:
                    reasons.add("private-network-not-internal")
    private_name = model["networks"]["private"]["name"]
    owned_ids = {r.identifier for r in observation.resources if r.kind == "container" and
                 r.facts["labels"].get("com.docker.compose.project") == artifact.project and
                 r.facts["labels"].get(f"{LABEL}.application") == application.id}
    for r in observation.resources:
        if r.kind == "network" and r.name == private_name and set(r.facts["containers"]) - owned_ids:
            reasons.add("private-network-foreign-attachments")
        if r.kind != "container":
            continue
        actual = r.facts["labels"]
        project_match = actual.get("com.docker.compose.project") == artifact.project
        name_match = r.name.startswith(artifact.project + "-") or r.name.startswith(artifact.project + "_")
        owner_match = actual.get(f"{LABEL}.application") == application.id
        if project_match or name_match or owner_match:
            name = actual.get("com.docker.compose.service")
            wanted = model["services"].get(name)
            if not wanted or not owned_resource(r, labels(application.id, name), artifact.project, name):
                reasons.add("container-ownership-conflict")
            elif r.name not in (f"{artifact.project}-{name}-1", f"{artifact.project}_{name}_1"):
                reasons.add("wrong-container-identity")
        if not project_match:
            volume_names = {v["name"] for v in model.get("volumes", {}).values()}
            if any(m.get("Name") in volume_names for m in r.facts["mounts"]):
                reasons.add("volume-foreign-attachment")
        for net, attachment in r.facts["networks"].items():
            if net == model.get("networks", {}).get("proxy", {}).get("name"):
                for name, service in model["services"].items():
                    desired_aliases = service["networks"].get("proxy", {}).get("aliases", [])
                    if set(desired_aliases) & set(attachment.get("Aliases") or []) and not (
                        project_match and actual.get("com.docker.compose.service") == name):
                        reasons.add("proxy-alias-conflict")
    return tuple(sorted(reasons))


def deployment_preview(desired, observed, plan, application):
    if build_reconciliation_plan(desired, observed) != plan:
        raise DockerError("stale-plan")
    app = next((a for a in desired.applications if a.id == application), None)
    if app is None:
        raise DockerError("unknown-application")
    entries = plan.document["entries"]
    # The executor rejects the whole reconciliation, including other applications.
    blockers = {reason for e in entries if e["status"] in ("blocked", "degraded") for reason in e["reasons"]}
    if plan.document["status"] in ("blocked", "degraded"):
        blockers.add("reconciliation-not-admissible")
    operations = []
    for name in startup_order(app):
        component = next(c for c in app.spec["components"] if c["name"] == name)
        relevant = [e for e in entries if e["application"] == app.id and e["component"] == name]
        action = next((e["action"] for e in relevant if e["action"] in ("create", "update-component")), "no-op")
        operations.append(ExecutionOperation(app.id, name, desired.git_revision, component["image"], action,
                                             "blocked" if blockers else "requires-checks", tuple(sorted(blockers))))
    return tuple(operations)


def execute(desired, observed, plan, application, adapter, provider, *, lock_directory="/tmp/portfolio-deployment-locks", lifecycle=None, lease=None):
    """Provider refresh() -> (admitted desired, full observed, DockerObservation).

    Local live provider cannot supply complete Stage 03 evidence yet. Offline fake
    providers and the isolated test harness are explicit test-only trust boundaries.
    Re-admission and full equality protect desired contents as well as Git SHA.
    """
    operations = deployment_preview(desired, observed, plan, application)
    def all_blocked(code):
        return tuple(ExecutionResult(o, "blocked", (code,)) for o in operations)
    try:
        with existing_or_host_lock(lease, lock_directory) as lease, ExitStack() as stack:
            if lifecycle is not None:
                stack.enter_context(lifecycle.open(lease, adapter, provider))
            fresh_desired, fresh_observed, docker = provider.refresh()
            if fresh_desired != desired:
                return all_blocked("desired-revision-or-content-changed")
            fresh_plan = build_reconciliation_plan(fresh_desired, fresh_observed)
            if fresh_plan != plan or fresh_observed != observed:
                return all_blocked("stale-plan-or-observation")
            if plan.document["status"] in ("blocked", "degraded"):
                return all_blocked("reconciliation-not-admissible")
            # The adapter must explicitly verify environment, endpoint and allowed project.
            app = next(a for a in desired.applications if a.id == application)
            artifact = render_compose(app, test_session=adapter.test_session)
            adapter.authorize(artifact)
            reasons = preflight_resources(app, artifact, docker)
            if reasons:
                return tuple(ExecutionResult(o, "blocked", reasons) for o in operations)
            containers = {}
            for r in docker.resources:
                if r.kind == "container" and r.facts["labels"].get("com.docker.compose.project") == artifact.project:
                    name = r.facts["labels"].get("com.docker.compose.service")
                    if name in containers:
                        return all_blocked("duplicate-component-containers")
                    containers[name] = r
            images = {i.reference: i for i in docker.images}
            # Validate actual before state against the plan, never only runtime labels.
            entries = plan.document["entries"]
            for o in operations:
                current = containers.get(o.component)
                if o.action == "create":
                    if current:
                        return all_blocked("stale-component-existence")
                    continue
                if not current:
                    return all_blocked("stale-component-existence")
                before = next((e["before"] for e in entries if e["application"] == app.id and
                               e["component"] == o.component and e["action"] == "update-component"), None)
                if o.action == "update-component" and (not before or not before.get("image")):
                    return all_blocked("previous-image-reference-unavailable")
                c = before or next(c for c in app.spec["components"] if c["name"] == o.component)
                old_spec = app.spec
                old_spec["components"] = [c if x["name"] == c["name"] else x for x in old_spec["components"]]
                old_app = type(app)(app.id, app.source_repository, encode(old_spec))
                old_artifact = render_compose(old_app, test_session=adapter.test_session)
                image = images.get(c["image"])
                if (not image or current.facts["state"].get("Status") != "running" or
                    current.facts["labels"].get(f"{LABEL}.runtime") != digest(c) or
                    not container_runtime(current, old_artifact, o.component, image)):
                    return all_blocked("effective-runtime-not-established")
            if lifecycle is not None:
                lifecycle.prepare(desired, app, operations, plan)
            results, stopped = [], False
            for o in operations:
                component = next(c for c in app.spec["components"] if c["name"] == o.component)
                if stopped:
                    results.append(ExecutionResult(o, "blocked", ("prior-operation-not-converged",)))
                    continue
                # Dependencies must already be observed healthy; just started is insufficient.
                dependencies = [containers.get(name) for name in component["dependsOn"]]
                dependency_blocked = (not lifecycle.dependencies_ready(component) if lifecycle is not None else
                    any(r is None or r.facts["state"].get("Status") != "running" or
                        r.facts["state"].get("Health", {}).get("Status") != "healthy" for r in dependencies))
                if dependency_blocked:
                    results.append(ExecutionResult(o, "blocked", ("dependency-readiness-unverified",),
                                                   next_checks=("fresh-discovery", "dependency-readiness")))
                    if lifecycle is not None:
                        lifecycle.block(o.component, "dependency-readiness-unverified")
                    stopped = True
                    continue
                if lifecycle is not None:
                    # The Stage 06 hook journals intent and delegates the same scoped adapter.up.
                    result = lifecycle.step(o, artifact)
                    results.append(result)
                    stopped = result.status != "succeeded"
                    continue
                if o.action == "no-op":
                    results.append(ExecutionResult(o, "no-op", ("effective-runtime-matches",)))
                    continue
                try:
                    adapter.up(artifact, o.component)
                    results.append(ExecutionResult(o, "applied-unverified", ("compose-command-completed",),
                                                   (f"{artifact.project}/{o.component}",), ("fresh-discovery", "readiness", "release-policy")))
                except DockerError as error:
                    results.append(ExecutionResult(o, "unknown" if error.unknown else "failed", (error.code,),
                                                   (f"{artifact.project}/{o.component}",), ("fresh-discovery",)))
                    stopped = True
                except Exception:
                    results.append(ExecutionResult(o, "unknown", ("adapter-outcome-unavailable",),
                                                   (f"{artifact.project}/{o.component}",), ("fresh-discovery",)))
                    stopped = True
            if lifecycle is not None:
                results = lifecycle.complete(results)
            return tuple(results)
    except DockerError as error:
        return all_blocked(error.code)
    except (RegistryValidationError, PlanningInputError, OSError):
        return all_blocked("fresh-state-unavailable")


@contextmanager
def existing_or_host_lock(lease, directory):
    """Trusted in-process caller may retain its issued lease across preflight."""
    if lease is None:
        with host_lock(directory) as acquired:
            yield acquired
    else:
        if not owns_host_lock(lease):
            raise DockerError('host-lock-ownership-required')
        yield lease
