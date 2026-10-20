"""Immutable normalized models and pure offline reconciliation comparison.

Canonical JSON strings are immutable value objects, not serialized live state.
Accessors return detached copies; no model retains mutable caller-owned objects.
"""

from dataclasses import dataclass
import json
import re


class PlanningInputError(ValueError):
    """Bounded diagnostics containing rules, never rejected input values."""


def canonical(value):
    """All arrays in the supported v1 contract have set/identity semantics."""
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return sorted((canonical(item) for item in value), key=encode)
    return value


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def revision(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise PlanningInputError("git revision must be a full lowercase 40-character SHA")
    return value


def normalized_spec(spec):
    spec = json.loads(encode(spec))
    for key in ("configRefs", "secretRefs"):
        spec.setdefault(key, [])
    spec.setdefault("ingress", None)
    for component in spec["components"]:
        for key in ("dependsOn", "configRefs", "secretRefs", "volumeMounts"):
            component.setdefault(key, [])
        component["proxyNetwork"] = bool(spec["ingress"] and spec["ingress"]["component"] == component["name"])
    return canonical(spec)


@dataclass(frozen=True)
class DesiredApplication:
    id: str
    source_repository: str
    spec_json: str

    @property
    def spec(self):
        return json.loads(self.spec_json)


@dataclass(frozen=True)
class DesiredState:
    git_revision: str
    applications: tuple[DesiredApplication, ...]


@dataclass(frozen=True)
class ObservedState:
    snapshot_json: str

    @property
    def document(self):
        return json.loads(self.snapshot_json)


@dataclass(frozen=True)
class ReconciliationPlan:
    plan_json: str

    @property
    def document(self):
        return json.loads(self.plan_json)

    def to_json(self):
        return self.plan_json + "\n"

    def to_text(self):
        document = self.document
        lines = [f"{document['status']}: offline plan, executed=false; revision {document['gitRevision']}"]
        for item in document["entries"]:
            lines.append(f"{item['application']}/{item['component'] or '@application'}: "
                         f"{item['action']} [{item['status']}] {','.join(item['reasons'])}")
        return "\n".join(lines) + "\n"


def owned(owner, app, component=None):
    return owner == {"manager": "portfolio-deployment", "application": app,
                     **({"component": component} if component else {})}


def build_reconciliation_plan(desired: DesiredState, observed: ObservedState):
    """Compare validated value models. No I/O, discovery or execution occurs here.

    Results are a diff, not an execution ordering or authorization. Blocking an
    application also blocks its otherwise possible changes; other apps stay scoped.
    """
    revision(desired.git_revision)
    snapshot = observed.document
    actual = {app["id"]: app for app in snapshot["applications"]}
    wanted = {app.id: app for app in desired.applications}
    entries = []
    complete = snapshot["scope"]["complete"] and snapshot["scope"]["trusted"]

    def add(app, component, action, reasons, before=None, after=None, status=None, warnings=()):
        checks = []
        if action in ("create", "update-component"):
            checks = ["artifact-platform-preflight", "configuration-reference-resolution", "fresh-owned-observation", "persistent-storage-ownership-and-migration-policy", "readiness"]
        elif action == "reference-inventory-change":
            checks = ["configuration-reference-resolution"]
        elif action in ("route-change", "route-only"):
            checks = ["fresh-owned-observation", "gateway-candidate-validation", "target-readiness"]
        entries.append({"application": app, "component": component, "action": action,
                        "status": status or ("changes" if checks else "no-op"),
                        "reasons": sorted(set(reasons)), "warnings": sorted(set(warnings)),
                        "before": before, "after": after, "requiredChecks": checks})

    for app_id in sorted(wanted.keys() | actual.keys()):
        start = len(entries)
        d = wanted.get(app_id)
        o = actual.get(app_id)
        if not complete:
            add(app_id, None, "blocked", ["incomplete-or-untrusted-snapshot"], status="blocked")
            continue
        if o and (o["existence"] == "unknown" or not owned(o["owner"], app_id)):
            add(app_id, None, "blocked", ["unknown-state-or-ownership-conflict"], status="blocked")
            continue
        present = o and o["existence"] == "present"
        if not d or not d.spec["enabled"]:
            if present:
                add(app_id, None, "removal-pending-approval" if not d else "disable-pending-approval",
                    ["separate-lifecycle-decision-required", "preserve-persistent-data"],
                    before={"existence": "present"}, after={"enabled": False} if d else None, status="blocked")
            else:
                add(app_id, None, "no-op", ["disabled-or-removed-and-absent"])
            continue
        spec = d.spec
        if not present:
            add(app_id, None, "create", ["application-absent"], after={key: value for key, value in spec.items() if key != "components"})
        warnings = []
        if spec["configRefs"] or spec["secretRefs"]:
            warnings.append("reference-identifiers-only-content-and-versions-unobserved")
        components = {c["name"]: c for c in spec["components"]}
        observed_components = {c["name"]: c for c in o["components"]} if present else {}
        if present:
            if o["storage"] is None or o["configRefs"] is None or o["secretRefs"] is None:
                add(app_id, None, "blocked", ["unknown-application-configuration"], status="blocked")
            elif canonical(o["storage"]) != spec["storage"]:
                add(app_id, None, "blocked", ["storage-change-requires-lifecycle-policy"],
                    o["storage"], spec["storage"], status="blocked")
            elif o["configRefs"] != spec["configRefs"] or o["secretRefs"] != spec["secretRefs"]:
                # Application declarations are reference inventory, not injections.
                add(app_id, None, "reference-inventory-change", ["reference-declarations-differ"],
                    {k:o[k] for k in ("configRefs", "secretRefs")},
                    {k:spec[k] for k in ("configRefs", "secretRefs")}, status="changes", warnings=warnings)
        for name in sorted(components.keys() | observed_components.keys()):
            c, oc = components.get(name), observed_components.get(name)
            if oc and (oc["existence"] == "unknown" or not owned(oc["owner"], app_id, name)):
                add(app_id, name, "blocked", ["unknown-state-or-ownership-conflict"], status="blocked")
                continue
            exists = oc and oc["existence"] == "present"
            if not c:
                if exists:
                    add(app_id, name, "removal-pending-approval", ["component-removed-from-declaration"], status="blocked")
                continue
            if not exists:
                add(app_id, name, "create", ["drift-missing-component" if present else "application-absent"],
                    after=c, warnings=warnings)
                continue
            if oc["health"] == "degraded":
                add(app_id, name, "health-observation", ["degraded-health-does-not-imply-recreate"], status="degraded")
            elif oc["health"] == "unknown":
                add(app_id, name, "blocked", ["health-unobserved"], status="blocked")
            identity, runtime = oc["imageIdentity"], oc["runtime"]
            if identity is None or runtime is None or not oc["runtimeComplete"]:
                add(app_id, name, "blocked", ["unknown-or-unsupported-runtime"], status="blocked")
                continue
            pull = identity["pullReference"]
            # A manifest digest can equal the pull digest directly; an index needs
            # explicit verified platform mapping evidence from the snapshot producer.
            if (identity["platform"] != runtime["platform"] or
                identity["relationship"] != "verified" or
                (identity["pullKind"] == "manifest" and pull.split("@", 1)[1] != identity["manifestDigest"])):
                add(app_id, name, "blocked", ["oci-identity-not-established"], status="blocked")
                continue
            before = {"name": name, "image": pull, **runtime}
            if canonical(before.get("volumeMounts", [])) != c["volumeMounts"]:
                add(app_id, name, "blocked", ["persistent-mount-change-requires-lifecycle-policy"], before, c, status="blocked")
            elif canonical(before) == c:
                add(app_id, name, "no-op", ["comparable-configuration-matches"], warnings=warnings)
            else:
                add(app_id, name, "update-component", ["effective-configuration-diff", "diff-origin-unproven"],
                    canonical(before), c, warnings=warnings)
        desired_route = spec["ingress"]
        route = o["route"] if present else {"existence": "absent", "configuration": None}
        if present and (route["existence"] == "unknown" or not owned(route["owner"], app_id)):
            add(app_id, None, "blocked", ["unknown-route-or-ownership-conflict"], status="blocked")
        else:
            before_route = route["configuration"] if route["existence"] == "present" else None
            if before_route != desired_route:
                network_change = any(e["action"] in ("create", "update-component") and
                                     (e["before"] is None or e["before"].get("proxyNetwork") != e["after"].get("proxyNetwork"))
                                     for e in entries[start:])
                add(app_id, None, "route-change" if network_change else "route-only",
                    ["ingress-diff", "network-membership-change" if network_change else "runtime-network-unchanged"],
                    before_route, desired_route)
            else:
                add(app_id, None, "no-op", ["route-matches"], warnings=warnings)
        # Dependency and route safety is application-scoped. Keep proposed diffs
        # reviewable, but never leave an actionable sibling in a blocked app.
        if any(e["status"] == "blocked" for e in entries[start:]):
            for item in entries[start:]:
                if item["status"] == "changes":
                    item["status"] = "blocked"
                    item["reasons"] = sorted(set(item["reasons"] + ["application-has-blockers"]))
    if not complete and not entries:
        add("@registry", None, "blocked", ["incomplete-or-untrusted-snapshot"], status="blocked")
    entries.sort(key=lambda e: (e["application"], e["component"] or "", e["action"]))
    def outcome(items):
        statuses = {e["status"] for e in items}
        return next((s for s in ("blocked", "degraded", "changes") if s in statuses), "no-op")

    applications = [{"id": app_id, "status": outcome(e for e in entries if e["application"] == app_id)}
                    for app_id in sorted({e["application"] for e in entries})]
    status = outcome(entries)
    return ReconciliationPlan(encode({"apiVersion": "portfolio.plan/v1", "gitRevision": desired.git_revision,
                                      "executed": False, "authorization": False, "status": status,
                                      "applications": applications, "entries": entries}))
