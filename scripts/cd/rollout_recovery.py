"""Root-only signed acceptance of an already running first ProjectRouter rollout."""
import json
import math
from pathlib import Path
import re
import time

from scripts.cd.bootstrap_containment import BootstrapContainment
from scripts.cd.signatures import authenticate
from scripts.deployment_lifecycle import runtime_identity
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.observations import require


def eligible(journal, run, expected_image, app):
    """Require exactly one failed first rollout with verified web readiness."""
    require(
        isinstance(run, str)
        and re.fullmatch(r"[0-9a-f]{32}", run) is not None,
        "rollout-recovery-run-invalid",
    )
    require(
        isinstance(expected_image, str)
        and re.fullmatch(
            r"ghcr\.io/danilfaritovich/project-router@sha256:[0-9a-f]{64}",
            expected_image,
        ) is not None,
        "rollout-recovery-image-invalid",
    )

    # The caller must supply the independently verified, installed app.
    # Candidate runtime_hash describes the preflight snapshot, NOT the
    # image-excluded application runtime identity stored in the journal.
    require(
        app.id == "project-router"
        and len(app.spec["components"]) == 1
        and app.spec["components"][0]["name"] == "web"
        and app.spec["components"][0]["image"] == expected_image,
        "rollout-recovery-application-mismatch",
    )

    journal.validate()
    rows = journal.rows()

    # No unrelated unfinished operations are allowed.
    unfinished = {
        row["run"]
        for row in rows
        if row["run_status"] not in ("succeeded", "recovered", "contained")
    }
    require(
        unfinished == {run},
        "rollout-recovery-unfinished-history",
    )

    # A separate consumed authorization without a run must also block
    # recovery. Checking incomplete runs alone is insufficient.
    pending = journal.authorization_recovery()
    require(
        len(pending) == 1
        and pending[0]["nonce"] == run
        and pending[0]["state"] == "recovery-required",
        "rollout-recovery-authorization-history-ineligible",
    )

    # This mechanism is exclusively for the FIRST ProjectRouter rollout.
    application_rows = [
        row for row in rows
        if row["application"] == "project-router"
    ]
    require(
        len(application_rows) == 1
        and application_rows[0]["run"] == run,
        "rollout-recovery-history-ineligible",
    )
    row = application_rows[0]

    approval = journal.db.execute(
        "SELECT * FROM authorizations WHERE nonce = ?",
        (run,),
    ).fetchone()
    require(
        approval is not None,
        "rollout-recovery-authorization-missing",
    )
    candidate = OperationCandidate(**json.loads(approval["payload"]))

    events = [
        dict(event)
        for event in journal.db.execute(
            "SELECT * FROM events WHERE run = ? ORDER BY sequence",
            (run,),
        )
    ]

    require(
        candidate.scope == "first-project-router-rollout"
        and candidate.identity == approval["candidate"]
        and approval["state"] == "recovery-required"
        and candidate.image == expected_image
        and row["revision"] == candidate.manager_sha
        and row["component"] == "web"
        and row["target"] == candidate.image
        and row["runtime"] == runtime_identity(app)
        and row["previous"] is None
        and row["run_status"] == "recovery-required"
        and row["run_reason"] == "gateway-unverified"
        and row["state"] == "succeeded"
        and row["reason"] == "readiness-verified"
        and row["readiness"] == "verified"
        and row["consecutive"] >= 2
        and row["probes"] >= row["consecutive"],
        "rollout-recovery-state-ineligible",
    )

    require(
        [event["state"] for event in events]
        == ["prepared", "applying", "verifying", "succeeded"]
        and [event["reason"] for event in events]
        == [
            "intent-recorded",
            "mutation-started",
            "mutation-completed",
            "readiness-verified",
        ],
        "rollout-recovery-events-ineligible",
    )

    require(
        journal.last_good("project-router", "web") is None
        and journal.production_bootstrap(candidate.host, candidate.daemon),
        "rollout-recovery-baseline-ineligible",
    )

    return candidate


RECOVERY_NAMESPACE = "portfolio-first-rollout-recovery-v1"

RECOVERY_FIELDS = {
    "version",
    "id",
    "scope",
    "run",
    "candidate",
    "host",
    "daemon",
    "manager_sha",
    "image",
    "policy_id",
    "inventory_sha256",
    "capsule_sha256",
    "journal_sha256",
    "observation_sha256",
    "not_before",
    "expires",
}


def verify_recovery_decision(envelope, *, keys, now=None):
    """Authenticate an exact, short-lived operator recovery decision.

    This is validation only. It does not authorize journal mutation.
    The execution layer must compare every field against fresh host facts.
    """
    decision, _ = authenticate(
        envelope,
        keys=keys,
        namespace=RECOVERY_NAMESPACE,
    )

    current = time.time() if now is None else now

    require(
        type(decision) is dict
        and set(decision) == RECOVERY_FIELDS
        and type(decision["version"]) is int
        and decision["version"] == 1
        and decision["scope"] == "first-project-router-rollout",
        "rollout-recovery-decision-invalid",
    )

    patterns = {
        "id": r"[0-9a-f]{32}",
        "run": r"[0-9a-f]{32}",
        "candidate": r"sha256:[0-9a-f]{64}",
        "host": r"[0-9a-f]{32}",
        "daemon": r"[A-Za-z0-9:_-]{1,128}",
        "manager_sha": r"[0-9a-f]{40}",
        "image": (
            r"ghcr\.io/danilfaritovich/"
            r"project-router@sha256:[0-9a-f]{64}"
        ),
        "policy_id": r"sha256:[0-9a-f]{64}",
        "inventory_sha256": r"[0-9a-f]{64}",
        "capsule_sha256": r"[0-9a-f]{64}",
        "journal_sha256": r"[0-9a-f]{64}",
        "observation_sha256": r"sha256:[0-9a-f]{64}",
    }

    require(
        all(
            type(decision[field]) is str
            and re.fullmatch(pattern, decision[field]) is not None
            for field, pattern in patterns.items()
        ),
        "rollout-recovery-decision-invalid",
    )

    require(
        type(current) in (int, float)
        and math.isfinite(current)
        and all(
            type(decision[field]) is int
            for field in ("not_before", "expires")
        )
        and 0 <= decision["not_before"] <= current
        < decision["expires"]
        and 0 < decision["expires"] - decision["not_before"] <= 300,
        "rollout-recovery-decision-expired",
    )

    return decision


def observe_live_release(candidate, app, provider, probes, policy, *,
                         public_postconditions=None, before_public=None, **timing):
    """Verify an existing release without changing Docker, Caddy or SQLite.

    Production callers must supply the installed, sealed content reader,
    authenticated production state provider and approved readiness adapter.
    This function cannot establish the trust of caller-supplied ports itself.
    """
    from scripts.compose import labels, render_compose
    from scripts.deployment import preflight_resources
    from scripts.docker_discovery import container_runtime, owned_resource
    from scripts.readiness import verify_readiness

    require(
        public_postconditions is not None
        and callable(getattr(public_postconditions, "verify", None)),
        "rollout-recovery-public-authority-required",
    )
    candidate.validate()
    require(
        candidate.scope == "first-project-router-rollout"
        and app.id == "project-router"
        and len(app.spec["components"]) == 1
        and app.spec["components"][0]["name"] == "web"
        and app.spec["components"][0]["image"] == candidate.image
        and app.spec["ingress"] is not None,
        "rollout-recovery-application-mismatch",
    )

    component = app.spec["components"][0]
    artifact = render_compose(app)

    def inspect():
        desired, observed, docker = provider.refresh()

        require(
            desired.git_revision == candidate.manager_sha
            and len(desired.applications) == 1
            and desired.applications[0] == app
            and provider.sample is not None
            and provider.sample.gateway_verified is True,
            "rollout-recovery-state-unverified",
        )

        applications = [
            a for a in observed.document["applications"]
            if a["id"] == app.id
        ]
        require(
            len(applications) == 1
            and len(observed.document["applications"]) == 1,
            "rollout-recovery-inventory-unverified",
        )
        actual = applications[0]

        require(
            actual["route"]["existence"] == "present"
            and actual["route"]["configuration"] == app.spec["ingress"]
            and len(actual["components"]) == 1
            and not preflight_resources(app, artifact, docker),
            "rollout-recovery-gateway-unverified",
        )

        observed_component = actual["components"][0]
        identity = observed_component["imageIdentity"]

        require(
            observed_component["name"] == "web"
            and observed_component["existence"] == "present"
            and observed_component["runtimeComplete"] is True
            and observed_component["runtime"] == {
                k: v for k, v in component.items()
                if k not in ("name", "image")
            }
            and observed_component["health"] == "healthy"
            and identity is not None
            and identity["pullReference"] == candidate.image
            and identity["relationship"] == "verified",
            "rollout-recovery-component-unverified",
        )

        images = [
            image for image in docker.images
            if image.reference == candidate.image
        ]
        containers = [
            resource for resource in docker.resources
            if resource.kind == "container"
            and resource.facts["labels"].get(
                "com.docker.compose.project"
            ) == artifact.project
            and resource.facts["labels"].get(
                "com.docker.compose.service"
            ) == "web"
        ]

        require(
            docker.complete is True
            and len(images) == 1
            and images[0].verified is True
            and images[0].platform == "linux/amd64"
            and identity["manifestDigest"] == images[0].manifest_digest
            and identity["platform"] == images[0].platform
            and len(containers) == 1,
            "rollout-recovery-image-unverified",
        )

        container = containers[0]

        require(
            owned_resource(
                container, labels(app.id, "web"), artifact.project, "web"
            )
            and container.facts["state"].get("Status") == "running"
            and container_runtime(container, artifact, "web", images[0]),
            "rollout-recovery-runtime-unverified",
        )

        return container.identifier

    # First complete trusted observation, including ACTIVE gateway evidence.
    pinned_id = inspect()

    # Repeat independent internal readiness against that exact container.
    evidence = verify_readiness(
        component, pinned_id, probes, policy, **timing
    )
    require(
        evidence.status == "verified"
        and evidence.reason == "readiness-verified"
        and evidence.consecutive >= 2,
        "rollout-recovery-readiness-unverified",
    )

    # Detect container replacement, route changes and lost readiness.
    require(
        inspect() == pinned_id,
        "rollout-recovery-container-changed",
    )

    # Independently verify pinned DNS, TLS, HTTPS content and a fresh
    # off-host signed witness after the second trusted runtime observation.
    # The production binding must supply the existing PublicPostconditions
    # with the installed release's pinned HTML digest and SignedPublicWitness.
    from scripts.gateway import desired_routes
    from scripts.reconciliation import DesiredState

    routes = desired_routes(DesiredState(candidate.manager_sha, (app,)))
    if before_public is not None:
        before_public()
    require(
        len(routes) == 1
        and public_postconditions.verify(
            routes, (candidate.host, candidate.daemon)
        ) == routes,
        "rollout-recovery-public-unverified",
    )

    # Stable evidence for a future operator-signed decision.
    # No timestamps, journal writes, or claimed release success.
    return {
        "application": app.id,
        "component": "web",
        "revision": candidate.manager_sha,
        "image": candidate.image,
        "container_id": pinned_id,
        "runtime": runtime_identity(app),
        "route": app.spec["ingress"],
        "readiness": "verified",
    }


class RolloutRecovery(BootstrapContainment):
    """Operator-reviewed recovery of an already-running first rollout.

    Production wiring must provide installed, sealed readers and trusted
    observation sources. Only execute may atomically promote verified journal state.
    """

    def __init__(
        self, *,
        installation,
        policy,
        lock,
        application_reader,
        observe,
        inventories,
        directory,
        state=None,
        keys=None,
        clock=time.time,
    ):
        self.installation = installation
        self.policy = policy
        self.lock = lock
        self.application_reader = application_reader
        self.observe = observe
        self.inventories = inventories
        self.directory = directory
        self.clock = clock
        self.state = Path(state) if state is not None else None
        self.keys = keys

    audit_prefix = 'first-rollout-recovery-'

    def _prepare_locked(self, lease):
        import uuid

        from scripts.cd.bootstrap_containment import digest, read_journal
        from scripts.production_integration.candidate import hashed

        self.installation.verify()
        self.policy.current()

        # In production this reader must come from InstalledContentReader,
        # never from the recovery request itself.
        app = self.application_reader()

        require(
            app.id == "project-router"
            and len(app.spec["components"]) == 1
            and app.spec["components"][0]["name"] == "web",
            "rollout-recovery-application-mismatch",
        )
        image = app.spec["components"][0]["image"]

        with read_journal(self.directory, lease) as (journal, raw):
            pending = journal.incomplete()
            require(
                len(pending) == 1,
                "rollout-recovery-single-run-required",
            )

            run = pending[0]["run"]
            candidate = eligible(journal, run, image, app)

            require(
                (
                    candidate.host,
                    candidate.daemon,
                    candidate.manager_sha,
                ) == (
                    self.policy.document["host"],
                    self.policy.document["daemon"],
                    self.installation.manager_sha,
                ),
                "rollout-recovery-installation-mismatch",
            )

            inventories = self.inventories()
            require(
                type(inventories) is dict
                and set(inventories) == {
                    "inventory_sha256",
                    "capsule_sha256",
                }
                and all(
                    type(value) is str
                    and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in inventories.values()
                ),
                "rollout-recovery-inventory-invalid",
            )

            observation = self.observe(candidate, app, lease)

            require(
                type(observation) is dict
                and set(observation) == {
                    "application",
                    "component",
                    "revision",
                    "image",
                    "container_id",
                    "runtime",
                    "route",
                    "readiness",
                }
                and observation["application"] == "project-router"
                and observation["component"] == "web"
                and observation["revision"] == candidate.manager_sha
                and observation["image"] == candidate.image
                and observation["runtime"] == runtime_identity(app)
                and observation["route"] == app.spec["ingress"]
                and observation["readiness"] == "verified"
                and type(observation["container_id"]) is str
                and bool(observation["container_id"]),
                "rollout-recovery-observation-invalid",
            )

            facts = {
                "version": 1,
                "scope": "first-project-router-rollout",
                "run": run,
                "candidate": candidate.identity,
                "host": candidate.host,
                "daemon": candidate.daemon,
                "manager_sha": candidate.manager_sha,
                "image": candidate.image,
                "policy_id": self.policy.identity,
                "journal_sha256": digest(raw),
                "observation_sha256": hashed(observation),
                **inventories,
            }

        now = int(self.clock())
        decision = {
            **facts,
            "id": uuid.uuid4().hex,
            "not_before": now - 5,
            "expires": now + 295,
        }

        require(
            set(decision) == RECOVERY_FIELDS,
            "rollout-recovery-decision-fields-invalid",
        )

        return {
            "status": "prepared",
            "decision": decision,
            "executorStarted": False,
            "bootstrapReplayed": False,
            "lastGoodCreated": False,
        }

    def prepare(self):
        with self.lock() as lease:
            return self._prepare_locked(lease)

    def execute(self, envelope):
        """Accept only a freshly reverified, operator-signed existing release.

        No Docker writes, Caddy reload, bootstrap, or executor invocation.
        Every database change is committed in one SQLite transaction.
        """
        from scripts.cd.bootstrap_containment import digest, journal_bytes
        from scripts.deployment_journal import Journal

        require(
            type(self.keys) is dict
            and bool(self.keys)
            and self.state is not None,
            "rollout-recovery-authority-unconfigured",
        )

        decision = verify_recovery_decision(
            envelope, keys=self.keys, now=self.clock()
        )

        def unchanged(fresh):
            excluded = {"id", "not_before", "expires"}
            expected = {
                key: value for key, value in decision.items()
                if key not in excluded
            }
            actual = {
                key: value for key, value in fresh.items()
                if key not in excluded
            }
            require(
                actual == expected,
                "rollout-recovery-signed-facts-changed",
            )

        with self.lock() as lease:
            fresh = self._prepare_locked(lease)["decision"]
            unchanged(fresh)

            original = journal_bytes(self.directory)
            require(
                digest(original) == decision["journal_sha256"],
                "rollout-recovery-journal-changed",
            )

            # Persist original bytes and signed authorization BEFORE SQL changes.
            # The inherited retention method uses exclusive, private,
            # fsynced files and refuses conflicting evidence.
            self.retain(decision, envelope, original)

            # A second independent observation must match the signed facts.
            fresh = self._prepare_locked(lease)["decision"]
            unchanged(fresh)

            require(
                journal_bytes(self.directory) == original,
                "rollout-recovery-journal-changed",
            )
            verify_recovery_decision(
                envelope, keys=self.keys, now=self.clock()
            )

            with Journal(self.directory, lease) as journal:
                with journal.transaction():
                    journal.validate()

                    app = self.application_reader()
                    candidate = eligible(
                        journal,
                        decision["run"],
                        decision["image"],
                        app,
                    )

                    require(
                        candidate.identity == decision["candidate"]
                        and candidate.manager_sha
                        == self.installation.manager_sha
                        and candidate.host == self.policy.document["host"]
                        and candidate.daemon == self.policy.document["daemon"],
                        "rollout-recovery-final-state-changed",
                    )

                    require(
                        journal.last_good("project-router", "web") is None,
                        "rollout-recovery-existing-lkg",
                    )

                    verify_recovery_decision(
                        envelope, keys=self.keys, now=self.clock()
                    )

                    now = time.time()
                    run = decision["run"]

                    updated = journal.db.execute(
                        """
                        UPDATE runs
                        SET status='succeeded',
                            reason='readiness-verified',
                            updated=?
                        WHERE id=?
                          AND application='project-router'
                          AND status='recovery-required'
                          AND reason='gateway-unverified'
                        """,
                        (now, run),
                    )
                    require(
                        updated.rowcount == 1,
                        "rollout-recovery-run-changed",
                    )

                    updated = journal.db.execute(
                        """
                        UPDATE authorizations
                        SET state='succeeded'
                        WHERE nonce=? AND state='recovery-required'
                        """,
                        (run,),
                    )
                    require(
                        updated.rowcount == 1,
                        "rollout-recovery-authorization-changed",
                    )

                    journal.db.execute(
                        """
                        INSERT INTO good(application, component, run)
                        VALUES ('project-router', 'web', ?)
                        """,
                        (run,),
                    )

                    # A validation failure rolls back ALL three SQL changes.
                    journal.validate()

            return {
                "status": "succeeded",
                "run": decision["run"],
                "candidate": decision["candidate"],
                "executorStarted": False,
                "bootstrapReplayed": False,
                "lastGoodCreated": True,
            }


def recovery_public_acceptance(
    candidate, app, *, standing, installation, installed_public
):
    """Build public acceptance from signed release and sealed CA pins.

    Production callers must supply the installed FixedPolicy.public.
    No live observation, signature issuance or server mutation happens here.
    """
    from dataclasses import replace

    from scripts.cd.policy import StandingPolicy
    from scripts.cd.release import ImageRelease
    from scripts.production_integration.public_postconditions import (
        PublicPolicy,
        PublicPostconditions,
    )
    from scripts.production_integration.public_witness import (
        SignedPublicWitness,
    )

    require(
        type(standing) is StandingPolicy
        and type(installed_public) is PublicPolicy,
        "rollout-recovery-public-binding-invalid",
    )

    installation.verify()
    standing.current()
    document = standing.document

    release = ImageRelease(
        **document["application"]["initial_release"]
    ).validate()

    require(
        candidate.scope == "first-project-router-rollout"
        and candidate.host == document["host"]
        and candidate.daemon == document["daemon"]
        and candidate.manager_sha == installation.manager_sha
        and candidate.producer_sha == release.source_sha
        and candidate.image == release.image
        and app.id == "project-router"
        and len(app.spec["components"]) == 1
        and app.spec["components"][0]["name"] == "web"
        and app.spec["components"][0]["image"] == release.image,
        "rollout-recovery-release-unbound",
    )

    require(
        installed_public.identity
        == (candidate.host, candidate.daemon)
        and dict(installation.files).get(
            installed_public.ca_file
        ) == installed_public.ca_sha256,
        "rollout-recovery-public-trust-unbound",
    )

    pinned_public = replace(
        installed_public,
        body_sha256=release.html_sha256,
    )
    pinned_public.validate()

    witness = SignedPublicWitness(
        installation,
        document["observer"]["key"],
    )

    return PublicPostconditions(
        pinned_public,
        external=witness,
    )


def recovery_runtime_pins(candidate, app, standing_review):
    """Accept runtime IDs only from independently signed standing policy.

    This returns configuration for future read-only source construction.
    It does not inspect Docker, grant deployment authority or modify state.
    """
    from scripts.cd.host import StandingReview
    from scripts.cd.release import ImageRelease

    require(
        type(standing_review) is StandingReview,
        "rollout-recovery-standing-review-required",
    )
    candidate.validate()
    review = standing_review.current()
    signed = review.standing.current().document
    release = ImageRelease(
        **signed["application"]["initial_release"]
    ).validate()

    require(
        candidate.scope == "first-project-router-rollout"
        and (candidate.host, candidate.daemon)
        == (review.policy.host, review.policy.daemon)
        == (signed["host"], signed["daemon"])
        and candidate.manager_sha == review.installation.manager_sha
        and candidate.producer_sha == release.source_sha
        and candidate.image == release.image
        and app.id == "project-router"
        and len(app.spec["components"]) == 1
        and app.spec["components"][0]["name"] == "web"
        and app.spec["components"][0]["image"] == release.image,
        "rollout-recovery-runtime-release-unbound",
    )

    pins = review.document["rollout"]
    require(
        type(pins) is dict
        and set(pins) == {
            "container_id",
            "proxy_id",
            "private_id",
            "infrastructure",
        },
        "rollout-recovery-runtime-pins-invalid",
    )

    ids = (
        pins["container_id"],
        pins["proxy_id"],
        pins["private_id"],
    )
    require(
        all(
            type(value) is str
            and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in ids
        )
        and len(set(ids)) == 3,
        "rollout-recovery-runtime-ids-invalid",
    )

    records = pins["infrastructure"]
    require(
        type(records) is list
        and 3 <= len(records) <= 16
        and all(
            type(row) is list
            and len(row) == 4
            and row[0] in ("container", "network")
            and type(row[1]) is str
            and re.fullmatch(r"[0-9a-f]{64}", row[1])
            and type(row[2]) is str
            and 0 < len(row[2]) <= 128
            and type(row[3]) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", row[3])
            for row in records
        ),
        "rollout-recovery-infrastructure-invalid",
    )

    identities = [row[1] for row in records]
    names = [row[2] for row in records]
    containers = {
        row[1] for row in records if row[0] == "container"
    }
    networks = {
        row[2]: row[1]
        for row in records if row[0] == "network"
    }

    require(
        len(set(identities)) == len(identities)
        and len(set(names)) == len(names)
        and containers == {pins["container_id"]}
        and networks.get("portfolio-proxy") == pins["proxy_id"]
        and networks.get("portfolio-project-router-private")
        == pins["private_id"],
        "rollout-recovery-infrastructure-unbound",
    )

    return {
        "container_id": ids[0],
        "proxy_id": ids[1],
        "private_id": ids[2],
        "infrastructure": tuple(tuple(row) for row in records),
    }


def build_recovery_state_reader(
    *, installation, standing_review, candidate, app,
    caddy, caddy_image, proxy_subnet, proxy_gateway,
    private_subnet, resolve_image, public_postconditions,
):
    """Construct read-only production sources, never an executor.

    Inputs are supplied by reviewed installed root-only wiring.
    No operation request may supply ports, policy, image resolver or runtime.
    """
    from scripts.cd.release import ImageRelease, ProjectedContent
    from scripts.docker_discovery import ImageEvidence
    from scripts.gateway import desired_routes
    from scripts.production_integration.active_config import (
        ActiveConfigTransport, InstalledConfigAdapter,
    )
    from scripts.production_integration.composition import (
        installed_active_channel,
    )
    from scripts.production_integration.docker import EngineContract
    from scripts.production_integration.gateway_read import ManagedGatewayReadPort
    from scripts.production_integration.http_read import InternalHTTPRead
    from scripts.production_integration.local_sources import installed_engine
    from scripts.production_integration.observation_window import (
        ROLLOUT_OBSERVATION_SECONDS,
    )
    from scripts.production_integration.observations import ProductionStateReader
    from scripts.production_integration.public_postconditions import (
        PublicPostconditions,
    )
    from scripts.production_integration.public_witness import SignedPublicWitness
    from scripts.production_integration.readiness import ProductionProbes
    from scripts.production_integration.runtime import CaddyRuntime, ProxyContract
    from scripts.production_integration.runtime_sources import (
        GatewaySource, LocalSourceAttestor, ReadinessSource,
        RuntimeInventory, RuntimeSource,
    )
    from scripts.production_integration.trust import SourceBinding, SourceTrust

    require(
        standing_review.installation is installation
        and type(caddy) is CaddyRuntime
        and type(caddy_image) is ImageEvidence
        and caddy_image.verified is True
        and caddy.image == caddy_image.reference
        and callable(resolve_image)
        and type(public_postconditions) is PublicPostconditions
        and type(public_postconditions.external) is SignedPublicWitness,
        "rollout-recovery-source-authority-invalid",
    )

    pins = recovery_runtime_pins(
        candidate, app, standing_review
    )
    signed = standing_review.standing.current().document
    release = ImageRelease(
        **signed["application"]["initial_release"]
    ).validate()

    require(
        standing_review.verify(
            caddy, caddy_image, (candidate.host, candidate.daemon)
        ) == caddy_image
        and public_postconditions.policy.identity
        == (candidate.host, candidate.daemon)
        and public_postconditions.policy.body_sha256
        == release.html_sha256
        and public_postconditions.external.public_key
        == signed["observer"]["key"],
        "rollout-recovery-source-binding-invalid",
    )

    proxy = ProxyContract(
        proxy_subnet, proxy_gateway, pins["proxy_id"]
    )
    proxy.validate()

    component = app.spec["components"][0]
    require(
        component["type"] == "http"
        and type(component["port"]) is int
        and type(component["health"]["path"]) is str,
        "rollout-recovery-health-contract-invalid",
    )

    engine = installed_engine(
        installation,
        EngineContract(
            "unix:///run/docker.sock",
            candidate.daemon,
            candidate.host,
            True,
        ),
    )

    content = ProjectedContent(
        installation, standing_review.standing, release
    )

    def desired():
        return content()[0]

    def image_from_registry(reference, inspected):
        require(
            reference == release.image,
            "rollout-recovery-image-outside-release",
        )
        evidence = resolve_image(reference, inspected)
        require(
            type(evidence) is ImageEvidence
            and evidence.verified is True
            and evidence.reference == release.image,
            "rollout-recovery-image-source-unverified",
        )
        return evidence

    channel, runtime = installed_active_channel(
        installation, engine, caddy, caddy_image,
        container_id=pins["container_id"],
        network_id=pins["proxy_id"],
    )

    active = ActiveConfigTransport(
        engine,
        channel,
        runtime,
        InstalledConfigAdapter(installation),
        lambda: desired_routes(desired()),
        max_age=ROLLOUT_OBSERVATION_SECONDS,
    )
    gateway = ManagedGatewayReadPort(
        active,
        image_evidence=caddy_image,
        expected_version=caddy.version,
        attestor=active,
    )

    inventory = RuntimeInventory(
        engine,
        (app,),
        image_from_registry,
        pins["infrastructure"],
    )

    probes = ProductionProbes(
        engine,
        InternalHTTPRead(
            private_subnet,
            {(component["port"], component["health"]["path"])},
        ),
        "project-router",
        private_subnet,
        network_id=pins["private_id"],
    )

    from scripts.cd.application import ActiveRoutes

    gateway_source = GatewaySource(
        engine,
        gateway,
        # Public acceptance runs once, after the stable runtime/readiness round.
        # Waiting on the external observer inside this round exhausts freshness.
        postconditions=ActiveRoutes(standing_review.standing),
    )
    readiness_source = ReadinessSource(
        inventory, {"project-router": probes}
    )
    runtime_source = RuntimeSource(
        inventory, gateway_source, readiness_source
    )

    ports = {
        "runtime": runtime_source,
        "gateway": gateway_source,
        "readiness": readiness_source,
    }
    trust = SourceTrust(
        tuple(
            SourceBinding(
                name, candidate.host, candidate.daemon, port.identity
            )
            for name, port in ports.items()
        ),
        LocalSourceAttestor(ports),
    )

    provider = ProductionStateReader(
        desired,
        engine,
        runtime_source,
        gateway_source,
        readiness_source,
        image_from_registry,
        proxy,
        source_trust=trust,
        max_age=ROLLOUT_OBSERVATION_SECONDS,
    ).provider()

    return provider, probes


def verify_original_release(candidate, fixed, *, github, registry, crypto):
    """Reverify the original rollout contract, including both signed sources.

    The initial image predates automatic CD's content-attestation contract.
    Its original sealed bindings and candidate remain the release authority;
    the signed recovery policy independently pins the public HTML digest.
    """
    from scripts.production_integration.artifacts import SourceRequest
    from scripts.production_integration.release import MANAGER, PRODUCER, ORIGIN, ReleaseVerifier
    from scripts.production_integration.release_collector import AuthenticatedReleaseCollector
    from scripts.production_integration.release_source import WorkflowCheck

    require(candidate.scope == 'first-project-router-rollout'
            and candidate.producer_sha == fixed.producer_sha == fixed.signer_sha
            and candidate.image == fixed.image,
            'rollout-recovery-original-release-unbound')
    checks = (*fixed.producer_checks,
        WorkflowCheck(MANAGER, 'Offline checks', 15368, '.github/workflows/offline.yml'))
    builder = 'https://github.com/' + PRODUCER + '/.github/workflows/publish.yml@' + fixed.signer_sha
    collector = AuthenticatedReleaseCollector(candidate, github=github, registry=registry, crypto=crypto,
        checks=checks, signer_sha=fixed.signer_sha, builder=builder,
        slsa_builder=fixed.slsa_builder, publication_check=fixed.publication_check)
    return ReleaseVerifier(candidate,
        required_checks=[(c.repository, c.name, c.app_id) for c in checks],
        allowed_builders=[builder], collector=collector).verify(
            SourceRequest(PRODUCER, candidate.producer_sha, candidate.manager_sha,
                          candidate.image, ORIGIN, MANAGER))


def installed_recovery(installation, standing, fixed):
    """Bind the capsule's sealed policy to read-only production adapters.

    No executor is constructed. Immutable provenance is checked once per
    invocation, before timed runtime observations. Requests supply no bindings.
    """
    from dataclasses import asdict

    from scripts.cd.administrative import CAPSULE, private
    from scripts.cd.application import CHALLENGE, STATE, release_ports
    from scripts.cd.bootstrap_containment import digest
    from scripts.cd.host import StandingReview
    from scripts.cd.release import ImageRelease, ProjectedContent
    from scripts.production_integration.artifacts import bind_engine_image, verify_oci_bytes
    from scripts.production_integration.caddy_release import CaddyReleaseCollector
    from scripts.production_integration.fixed_bindings import FixedPolicy, GH, ROOT, TOKEN
    from scripts.production_integration.gateway_write import durable_replace
    from scripts.production_integration.locking import production_host_lock, require_production_lease
    from scripts.production_integration.operator_approval import TRUST, canonical, trust_keys
    from scripts.production_integration.release import MANAGER
    from scripts.production_integration.release_crypto import AttestationVerifier
    from scripts.production_integration.release_source import WorkflowCheck
    from scripts.production_integration.release_transport import GHCRRead, HTTPSRead, IMAGE_REPOSITORY
    from scripts.production_integration.runtime import CaddyRuntime, RuntimePaths
    from scripts.readiness import ReadinessPolicy

    require(type(fixed) is FixedPolicy, 'rollout-recovery-fixed-policy-required')
    installation.verify()
    pins = dict(installation.files)
    require(pins.get(GH) == fixed.gh_sha256
            and pins.get(ROOT) == fixed.sigstore_root_sha256
            and pins.get(fixed.public.ca_file) == fixed.public.ca_sha256,
            'rollout-recovery-external-trust-unbound')
    review = StandingReview(installation, standing)
    release = ImageRelease(**standing.document['application']['initial_release']).validate()
    content = ProjectedContent(installation, standing, release)
    sources = None

    def observe(candidate, app, lease):
        nonlocal sources
        require_production_lease(lease)
        if sources is None:
            github, registry, crypto, context = release_ports(fixed)
            verify_original_release(candidate, fixed, github=github, registry=registry, crypto=crypto)
            blobs = verify_oci_bytes(release.image, IMAGE_REPOSITORY, registry.collect(release.image))
            caddy = CaddyRuntime(fixed.caddy_image, fixed.caddy_version, RuntimePaths(
                *(f'/srv/portfolio/{p}' for p in ('state', 'locks', 'journal', 'caddy-config',
                                                 'certificates', 'caddy-data')),
                fixed.certificate_budget_bytes))
            token = private(TOKEN, 4096).decode('ascii').strip()
            caddy_registry = GHCRRead(HTTPSRead(context=context),
                repository='ghcr.io/danilfaritovich/portfolio-caddy',
                registry_auth=(fixed.ghcr_user, token))
            caddy_image = CaddyReleaseCollector(github=github, registry=caddy_registry,
                crypto=AttestationVerifier(GH, fixed.gh_sha256, ROOT,
                    fixed.sigstore_root_sha256, repository=MANAGER),
                checks=(WorkflowCheck(MANAGER, 'Offline checks', 15368, '.github/workflows/offline.yml'),),
                publication_check=WorkflowCheck(MANAGER, 'Publish trusted Caddy / Publish', 15368,
                    '.github/workflows/caddy-image.yml')).collect(caddy, fixed.caddy_source_sha)
            public = recovery_public_acceptance(candidate, app, standing=standing,
                installation=installation, installed_public=fixed.public)
            provider, probes = build_recovery_state_reader(installation=installation,
                standing_review=review, candidate=candidate, app=app, caddy=caddy,
                caddy_image=caddy_image, proxy_subnet=fixed.proxy_subnet,
                proxy_gateway=fixed.proxy_gateway, private_subnet=fixed.private_subnet,
                resolve_image=lambda reference, inspected: bind_engine_image(blobs, inspected),
                public_postconditions=public)
            sources = provider, probes, public
        provider, probes, public = sources
        # The observer receives a challenge, never an assertion of success.
        # It independently checks DNS/TLS/content before signing a short-lived receipt.
        import base64
        from scripts.gateway import desired_routes
        from scripts.reconciliation import DesiredState
        routes = desired_routes(DesiredState(candidate.manager_sha, (app,)))
        def challenge():
            installation.verify()
            standing.current()
            with Path(fixed.public.ca_file).open('rb') as stream:
                ca = stream.read(1024 * 1024 + 1)
            require(0 < len(ca) <= 1024 * 1024 and digest(ca) == fixed.public.ca_sha256,
                    'rollout-recovery-ca-changed')
            durable_replace(STATE, CHALLENGE.name, canonical({
                'policy': asdict(public.policy), 'routes': [asdict(r) for r in routes],
                'ca': base64.b64encode(ca).decode(),
                'created': int(time.time()), 'policy_id': standing.identity}))
        return observe_live_release(candidate, app, provider, probes, ReadinessPolicy(),
                                    public_postconditions=public, before_public=challenge)

    return RolloutRecovery(installation=installation, policy=standing,
        lock=production_host_lock, application_reader=lambda: content()[0].applications[0],
        observe=observe, inventories=lambda: {
            'inventory_sha256': digest(private(Path('/opt/portfolio-deployment/installation.json'), 2*1024*1024)),
            'capsule_sha256': digest(private(CAPSULE / 'capsule.json', 2*1024*1024))},
        directory=Path('/srv/portfolio/journal'), state=STATE,
        keys=trust_keys(private(TRUST, 8192)))


def main():
    import base64
    import os
    import sys

    from scripts.cd.application import installed_policy
    from scripts.cd.bridge import installation
    from scripts.production_integration.operator_approval import canonical, closed_json

    require(os.geteuid() == 0, 'rollout-recovery-root-required')
    request = closed_json(sys.stdin.buffer.read(131073), 131072)
    require(request == {'op': 'prepare'} or
            (type(request) is dict and set(request) == {'op', 'envelope'}
             and request['op'] == 'execute' and type(request['envelope']) is str),
            'rollout-recovery-operation-invalid')
    envelope = (base64.b64decode(request['envelope'], validate=True)
                if request['op'] == 'execute' else None)
    import cd_bindings
    installed = installation()
    operation = installed_recovery(installed, installed_policy(installed), cd_bindings.POLICY)
    result = operation.prepare() if envelope is None else operation.execute(envelope)
    print(canonical(result).decode())
    return 0
