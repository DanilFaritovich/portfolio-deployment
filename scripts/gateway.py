"""Pure Stage 04 route projection, deterministic rendering and conservative preview."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from scripts.reconciliation import (PlanningInputError, build_reconciliation_plan,
                                    encode, owned, revision)


@dataclass(frozen=True)
class GatewayRoute:
    application: str
    component: str
    hostname: str
    port: int
    alias: str
    manager: str = "portfolio-deployment"
    git_revision: str | None = None

    def __post_init__(self):
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        identifier = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
        if (not re.fullmatch(identifier, self.application) or len(self.application) > 32 or
            not re.fullmatch(identifier, self.component) or len(self.component) > 32):
            raise PlanningInputError("invalid gateway identity")
        if (len(self.hostname) > 253 or not re.fullmatch(
                rf"(?:{label}\.)+[a-z]{{1,63}}", self.hostname)):
            raise PlanningInputError("invalid gateway hostname")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise PlanningInputError("invalid gateway port")
        if self.alias != f"{self.application}-{self.component}" or len(self.alias) > 63:
            raise PlanningInputError("invalid derived Docker DNS alias")
        if self.manager != "portfolio-deployment":
            raise PlanningInputError("gateway route ownership conflict")
        if self.git_revision is not None:
            revision(self.git_revision)

    @property
    def key(self):
        # Git revision is provenance, never a routing parameter.
        return self.application, self.component, self.hostname, self.port, self.alias


def route_set(routes):
    routes = tuple(sorted(routes, key=lambda r: r.key))
    for field in ("hostname", "alias", "application"):
        values = [getattr(r, field) for r in routes]
        if len(values) != len(set(values)):
            raise PlanningInputError("duplicate gateway hostname, alias or application")
    return routes


def make_route(app_id, ingress, git_revision=None):
    return GatewayRoute(app_id, ingress["component"], ingress["hostname"], ingress["port"],
                        f"{app_id}-{ingress['component']}", git_revision=git_revision)


def desired_routes(desired):
    """Admission belongs to build_desired_state; verify only projection invariants."""
    routes = []
    for app in desired.applications:
        spec = app.spec
        ingress = spec["ingress"]
        if not ingress:
            continue
        target = next((c for c in spec["components"] if c["name"] == ingress["component"]), None)
        if not target or target["type"] != "http" or target.get("port") != ingress["port"]:
            raise PlanningInputError("gateway ingress/component/port inconsistency")
        if not target["proxyNetwork"]:
            raise PlanningInputError("gateway ingress requires proxy network membership")
        # Disabled declarations still reserve identities, but never request activation.
        routes.append(make_route(app.id, ingress, desired.git_revision))
    enabled = {a.id for a in desired.applications if a.spec["enabled"]}
    return tuple(r for r in route_set(routes) if r.application in enabled)


def render_caddyfile(routes):
    """None is an explicit no-routes outcome, never an assumed valid empty config."""
    routes = route_set(routes)
    if not routes:
        return None
    return "\n\n".join(
        f"{r.hostname} {{\n    reverse_proxy {r.alias}:{r.port}\n}}"
        for r in sorted(routes, key=lambda r: (r.hostname, r.application))) + "\n"


def configuration(routes):
    routes = route_set(routes)
    content = render_caddyfile(routes)
    return {"routes": [asdict(r) for r in routes], "caddyfile": content,
            "outcome": "routes" if routes else "no-routes",
            "sha256": sha256(content.encode()).hexdigest() if content is not None else None}


@dataclass(frozen=True)
class GatewayCandidate:
    candidate_json: str

    @property
    def document(self):
        return json.loads(self.candidate_json)

    def to_json(self):
        return self.candidate_json + "\n"


def build_gateway_candidate(desired, observed, plan):
    """Adapt exactly the supplied Stage 03 result; never treat preview as activation.

    No Stage 04 input carries completed deployment/activation permissions. Therefore
    new/network routes are pending; the retained candidate is current, not desired.
    Route-only proposals report changes for review; activation remains gated and
    the selected candidate retains the current routes.
    """
    revision(desired.git_revision)
    if plan != build_reconciliation_plan(desired, observed):
        raise PlanningInputError("gateway requires the matching Stage 03 plan")
    wanted = desired_routes(desired)
    snapshot = observed.document
    reasons = set()
    complete = snapshot["scope"]["complete"] and snapshot["scope"]["trusted"]
    if not complete:
        reasons.add("incomplete-or-untrusted-snapshot")
    current = []
    for app in snapshot["applications"]:
        route = app["route"]
        if (app["existence"] == "unknown" or route["existence"] == "unknown" or
            not owned(app["owner"], app["id"]) or not owned(route["owner"], app["id"])):
            complete = False
            reasons.add("unknown-route-or-ownership-conflict")
        elif route["existence"] == "present":
            ingress = route["configuration"]
            target = next((c for c in app["components"] if c["name"] == ingress["component"]), None)
            runtime = target["runtime"] if target else None
            if (not target or target["existence"] != "present" or not runtime or
                runtime["type"] != "http" or runtime.get("port") != ingress["port"] or
                not runtime["proxyNetwork"]):
                complete = False
                reasons.add("observed-ingress-runtime-inconsistent-or-unknown")
            current.append(make_route(app["id"], route["configuration"]))
    try:
        current = route_set(current)
    except PlanningInputError:
        complete = False
        reasons.add("conflicting-observed-routes")
    current_by_id = {r.application: r for r in current}
    wanted_by_id = {r.application: r for r in wanted}
    occupied = {r.hostname: r.application for r in current}
    conflicts = {r.application for r in wanted if occupied.get(r.hostname, r.application) != r.application}
    if conflicts:
        reasons.add("desired-hostname-owned-by-another-active-route")
    outcomes = []
    document = plan.document
    for app_id in sorted({a.id for a in desired.applications} | {a["id"] for a in snapshot["applications"]}):
        entries = [e for e in document["entries"] if e["application"] == app_id]
        old, new = current_by_id.get(app_id), wanted_by_id.get(app_id)
        lifecycle = any(e["action"] in ("disable-pending-approval", "removal-pending-approval") for e in entries)
        blocked = any(e["status"] in ("blocked", "degraded") for e in entries)
        same = (old.key if old else None) == (new.key if new else None)
        checks = set()
        if not complete or blocked or app_id in conflicts:
            status = "blocked"
        elif same:
            status = "no-op"
        else:
            status = "pending"
        if lifecycle:
            kind = "pending-lifecycle-approval"
            checks.add("lifecycle-approval")
        elif old and not new:
            kind = "pending-removal-decision"
            checks.add("lifecycle-approval")
        elif not same:
            kind = next((e["action"] for e in entries if e["action"] in ("route-only", "route-change")), "pending-activation")
            checks.update(("target-readiness", "upstream-reachability", "deployment-permissions", "route-switch-approval"))
            if kind == "route-change" or not old:
                checks.update(("components-created", "proxy-network-membership"))
        else:
            kind = "route-matches"
        if not same:
            checks.update(("fresh-owned-observation", "official-caddy-validation", "serialized-reload-and-recovery"))
        if status == "pending" and kind == "route-only":
            status = "changes"
        outcomes.append({"application": app_id, "status": status, "kind": kind,
                         "before": asdict(old) if old else None, "desired": asdict(new) if new else None,
                         "reasons": sorted({r for e in entries for r in e["reasons"]}),
                         "requiredChecks": sorted(checks)})
    blocked = not complete or bool(conflicts) or document["status"] in ("blocked", "degraded")
    status = next((state for state in ("blocked", "pending", "changes")
                   if (state == "blocked" and blocked) or any(e["status"] == state for e in outcomes)), "no-op")
    if document["status"] in ("blocked", "degraded"):
        reasons.add("stage-03-plan-not-admissible")
    if status in ("pending", "changes"):
        reasons.add("activation-evidence-not-provided")
    return GatewayCandidate(encode({"apiVersion": "portfolio.gateway-candidate/v1",
        "gitRevision": desired.git_revision, "status": status, "applied": False, "authorization": False,
        "gatewayChangeRequired": None if not complete else ({r.key for r in current} != {r.key for r in wanted}),
        "candidateDiffersFromCurrent": False if complete else None,
        "desired": configuration(wanted), "current": configuration(current) if complete else None,
        "candidate": configuration(current) if complete else None,
        "outcomes": outcomes, "reasons": sorted(reasons),
        "requiredChecks": sorted({c for e in outcomes for c in e["requiredChecks"]}),
        "officialValidation": "not-performed", "reconciliationStatus": document["status"]}))
