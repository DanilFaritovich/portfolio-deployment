from scripts.reconciliation import DesiredState
"""Read-only first-rollout eligibility tests. No production access."""
from dataclasses import asdict, replace

import pytest

from scripts.cd.rollout_recovery import eligible
from scripts.deployment_journal import Journal, JournalError
from scripts.docker_adapter import DockerError
from scripts.reconciliation import encode
from test_production_contracts import registration
from test_production_integration import router
from test_production_execution import setup, approve


@pytest.fixture
def failed_rollout(setup):
    candidate, envelope = approve(setup)

    def fail_after_gateway_activation(*args, **kwargs):
        setup.world.route = True
        raise DockerError("gateway-verification-failed", True)

    setup.world.activate = fail_after_gateway_activation

    result = setup.executor.run(candidate, envelope)
    assert result.status == "recovery-required", result

    with setup.lock() as lease, Journal(setup.directory, lease) as journal:
        row = next(
            r for r in journal.rows()
            if r["application"] == "project-router"
        )
        assert row["state"] == "succeeded"
        assert row["run_reason"] == "gateway-unverified"

    return setup, candidate


def test_valid_failed_rollout_is_eligible_without_mutation(failed_rollout):
    world, candidate = failed_rollout
    run = "1" * 32

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        before = (
            len(journal.rows()),
            journal.db.execute("SELECT count(*) FROM events").fetchone()[0],
        )

        assert eligible(journal, run, candidate.image, world.world.app) == candidate

        after = (
            len(journal.rows()),
            journal.db.execute("SELECT count(*) FROM events").fetchone()[0],
        )
        assert after == before
        assert journal.incomplete()
        assert journal.last_good("project-router", "web") is None


@pytest.mark.parametrize("change", [
    "wrong-run",
    "wrong-image",
    "wrong-scope",
    "wrong-authorization",
    "missing-bootstrap-lkg",
])
def test_invalid_recovery_is_rejected(failed_rollout, change):
    world, candidate = failed_rollout
    run = "1" * 32
    image = candidate.image

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        if change == "wrong-run":
            run = "9" * 32
        elif change == "wrong-image":
            image = image[:-1] + ("0" if image[-1] != "0" else "1")
        elif change == "wrong-scope":
            changed = replace(candidate, scope="update-project-router")
            journal.db.execute(
                "UPDATE authorizations SET candidate=?,payload=? WHERE nonce=?",
                (
                    changed.identity,
                    __import__("json").dumps(vars(changed), sort_keys=True),
                    run,
                ),
            )
        elif change == "wrong-authorization":
            journal.db.execute(
                "UPDATE authorizations SET state='blocked' WHERE nonce=?",
                (run,),
            )
        elif change == "missing-bootstrap-lkg":
            journal.db.execute(
                "DELETE FROM good WHERE application='manager-bootstrap'"
            )

        with pytest.raises((DockerError, JournalError)):
            eligible(journal, run, image, world.world.app)


def test_changed_application_runtime_is_rejected(failed_rollout):
    world, candidate = failed_rollout
    spec = world.world.app.spec
    spec["components"][0]["port"] = 8081
    changed_app = replace(world.world.app, spec_json=encode(spec))

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        with pytest.raises(DockerError, match="rollout-recovery-state-ineligible"):
            eligible(journal, "1" * 32, candidate.image, changed_app)

        assert journal.last_good("project-router", "web") is None


def test_extra_pending_authorization_blocks_recovery(failed_rollout):
    world, candidate = failed_rollout
    another = replace(candidate, plan_hash="sha256:" + "f" * 64)

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        journal.db.execute(
            "INSERT INTO authorizations VALUES (?,?,?,?,?,?,?)",
            (
                "2" * 32,
                another.identity,
                "operator",
                90,
                150,
                "consumed",
                encode(asdict(another)),
            ),
        )

        with pytest.raises(
            DockerError,
            match="rollout-recovery-authorization-history-ineligible",
        ):
            eligible(journal, "1" * 32, candidate.image, world.world.app)


def test_modified_event_history_is_rejected(failed_rollout):
    world, candidate = failed_rollout

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        journal.db.execute(
            """
            UPDATE events
            SET reason = 'gateway-unverified'
            WHERE run = ? AND state = 'prepared'
            """,
            ("1" * 32,),
        )

        with pytest.raises(JournalError):
            eligible(journal, "1" * 32, candidate.image, world.world.app)


from scripts.cd.rollout_recovery import (
    RECOVERY_NAMESPACE,
    verify_recovery_decision,
)
from scripts.cd.signatures import sign
from test_cd import identities


@pytest.fixture
def recovery_decision():
    return {
        "version": 1,
        "id": "f" * 32,
        "scope": "first-project-router-rollout",
        "run": "1" * 32,
        "candidate": "sha256:" + "a" * 64,
        "host": "b" * 32,
        "daemon": "daemon",
        "manager_sha": "c" * 40,
        "image": (
            "ghcr.io/danilfaritovich/project-router@sha256:"
            + "d" * 64
        ),
        "policy_id": "sha256:" + "e" * 64,
        "inventory_sha256": "a" * 64,
        "capsule_sha256": "b" * 64,
        "journal_sha256": "c" * 64,
        "observation_sha256": "sha256:" + "d" * 64,
        "not_before": 990,
        "expires": 1890,
    }


def signed_recovery(decision, identities, role="operator",
                    namespace=RECOVERY_NAMESPACE):
    return sign(
        decision,
        principal=role,
        key=identities[role][0],
        namespace=namespace,
    )


def test_valid_operator_signature(identities, recovery_decision):
    envelope = signed_recovery(recovery_decision, identities)

    result = verify_recovery_decision(
        envelope,
        keys={"operator": identities["operator"][1]},
        now=1500,
    )

    assert result == recovery_decision


@pytest.mark.parametrize("field,value", [
    ("version", True),
    ("scope", "manager-upgrade"),
    ("run", "wrong-run"),
    ("host", "wrong-host"),
    ("image", "latest"),
    ("journal_sha256", "0"),
    ("observation_sha256", "invalid"),
    ("not_before", 1001),
    ("expires", 1000),
    ("expires", 1891),
])
def test_invalid_signed_decision_is_rejected(
    identities, recovery_decision, field, value
):
    decision = dict(recovery_decision)
    decision[field] = value

    envelope = signed_recovery(decision, identities)

    with pytest.raises(DockerError):
        verify_recovery_decision(
            envelope,
            keys={"operator": identities["operator"][1]},
            now=1000,
        )


@pytest.mark.parametrize("role,namespace", [
    ("automation", RECOVERY_NAMESPACE),
    ("observer", RECOVERY_NAMESPACE),
    ("operator", "portfolio-bootstrap-containment-v1"),
])
def test_untrusted_signature_is_rejected(
    identities, recovery_decision, role, namespace
):
    envelope = signed_recovery(
        recovery_decision,
        identities,
        role=role,
        namespace=namespace,
    )

    with pytest.raises(DockerError):
        verify_recovery_decision(
            envelope,
            keys={"operator": identities["operator"][1]},
            now=1000,
        )


def test_extra_fields_are_rejected(identities, recovery_decision):
    decision = {
        **recovery_decision,
        "reset_journal": True,
    }

    envelope = signed_recovery(decision, identities)

    with pytest.raises(DockerError):
        verify_recovery_decision(
            envelope,
            keys={"operator": identities["operator"][1]},
            now=1000,
        )


from scripts.cd.rollout_recovery import observe_live_release
from scripts.readiness import ProbeResult
from test_readiness_recovery import Clock, POLICY, Probes


class OfflinePublic:
    """Offline test double, never a production trust source."""

    def __init__(self, outcome="allowed"):
        self.outcome = outcome
        self.calls = 0

    def verify(self, routes, identity):
        self.calls += 1
        if self.outcome == "denied":
            raise DockerError("test-public-witness-invalid")
        if self.outcome == "wrong":
            return ()
        assert len(routes) == 1
        assert routes[0].hostname == "danil-miftakhov.dev"
        assert len(identity) == 2
        return routes


def observe_fixture(world, candidate, probes=None):
    clock = Clock()
    return observe_live_release(
        candidate,
        world.world.app,
        world.world.provider,
        probes if probes is not None else Probes(),
        POLICY,
        public_postconditions=OfflinePublic(),
        clock=clock.now,
        sleep=clock.sleep,
    )


def test_existing_live_release_is_verified_without_mutation(failed_rollout):
    world, candidate = failed_rollout
    calls_before = list(world.world.calls)

    result = observe_fixture(world, candidate)

    assert result["image"] == candidate.image
    assert result["container_id"] == world.world.container.identifier
    assert result["readiness"] == "verified"
    assert result["runtime"]
    assert world.world.calls == calls_before

    with world.lock() as lease, Journal(world.directory, lease) as journal:
        assert journal.incomplete()
        assert journal.last_good("project-router", "web") is None


@pytest.mark.parametrize("failure", [
    "missing-container",
    "missing-route",
    "unverified-image",
    "wrong-revision",
    "wrong-target",
])
def test_unverified_live_state_is_rejected(failed_rollout, failure):
    world, candidate = failed_rollout

    if failure == "missing-container":
        world.world.present = False
    elif failure == "missing-route":
        world.world.route = False
    elif failure == "unverified-image":
        world.world.image = replace(world.world.image, verified=False)
    elif failure == "wrong-revision":
        candidate = replace(candidate, manager_sha="f" * 40)
    elif failure == "wrong-target":
        candidate = replace(
            candidate,
            image="ghcr.io/danilfaritovich/project-router@sha256:" + "f" * 64,
        )

    with pytest.raises(DockerError):
        observe_fixture(world, candidate)


def test_container_replacement_during_readiness_is_rejected(failed_rollout):
    world, candidate = failed_rollout

    class ReplacingProbes:
        def probe(self, component, container_id, *, timeout, follow_redirects):
            world.world.container = replace(
                world.world.container,
                identifier="f" * 64,
            )
            return ProbeResult(True, 204, "healthy")

    with pytest.raises(
        DockerError, match="rollout-recovery-container-changed"
    ):
        observe_fixture(world, candidate, ReplacingProbes())


def test_failed_live_readiness_is_rejected(failed_rollout):
    world, candidate = failed_rollout

    with pytest.raises(
        DockerError, match="rollout-recovery-readiness-unverified"
    ):
        observe_fixture(world, candidate, Probes(fail_name="web"))


from types import SimpleNamespace

from scripts.cd.bootstrap_containment import journal_bytes
from scripts.cd.rollout_recovery import RolloutRecovery


@pytest.fixture
def recovery_world(failed_rollout, tmp_path):
    world, candidate = failed_rollout

    policy = SimpleNamespace(
        document={
            "host": candidate.host,
            "daemon": candidate.daemon,
        },
        identity="sha256:" + "9" * 64,
    )
    policy.current = lambda: policy

    installation = SimpleNamespace(
        manager_sha=candidate.manager_sha,
        verify=lambda: None,
    )

    def observe(current_candidate, app, lease):
        assert lease.fd >= 0
        assert current_candidate == candidate
        assert app == world.world.app

        clock = Clock()
        return observe_live_release(
            current_candidate,
            app,
            world.world.provider,
            Probes(),
            POLICY,
            public_postconditions=OfflinePublic(),
            clock=clock.now,
            sleep=clock.sleep,
        )

    operation = RolloutRecovery(
        installation=installation,
        policy=policy,
        lock=world.lock,
        application_reader=lambda: world.world.app,
        observe=observe,
        inventories=lambda: {
            "inventory_sha256": "a" * 64,
            "capsule_sha256": "b" * 64,
        },
        directory=world.directory,
        clock=lambda: 1000,
    )

    return SimpleNamespace(
        world=world,
        candidate=candidate,
        operation=operation,
        policy=policy,
        installation=installation,
    )


def test_prepare_binds_exact_journal_and_live_observation(
    recovery_world, identities
):
    w = recovery_world
    original = journal_bytes(w.world.directory)
    calls = list(w.world.world.calls)

    prepared = w.operation.prepare()
    decision = prepared["decision"]

    assert decision["expires"] - decision["not_before"] == 900

    assert prepared["status"] == "prepared"
    assert prepared["executorStarted"] is False
    assert prepared["bootstrapReplayed"] is False
    assert prepared["lastGoodCreated"] is False
    assert decision["run"] == "1" * 32
    assert decision["candidate"] == w.candidate.identity
    assert decision["image"] == w.candidate.image
    assert decision["journal_sha256"] == __import__(
        "hashlib"
    ).sha256(original).hexdigest()

    assert journal_bytes(w.world.directory) == original
    assert w.world.world.calls == calls

    envelope = signed_recovery(decision, identities)
    verified = verify_recovery_decision(
        envelope,
        keys={"operator": identities["operator"][1]},
        now=1000,
    )
    assert verified == decision


@pytest.mark.parametrize("failure", [
    "missing-container",
    "missing-route",
    "changed-manager",
    "changed-policy-host",
])
def test_prepare_rejects_changed_state(recovery_world, failure):
    w = recovery_world
    original = journal_bytes(w.world.directory)

    if failure == "missing-container":
        w.world.world.present = False
    elif failure == "missing-route":
        w.world.world.route = False
    elif failure == "changed-manager":
        w.installation.manager_sha = "f" * 40
    elif failure == "changed-policy-host":
        w.policy.document["host"] = "f" * 32

    with pytest.raises(DockerError):
        w.operation.prepare()

    assert journal_bytes(w.world.directory) == original


def configure_execution(recovery_world, identities, tmp_path):
    w = recovery_world
    state = tmp_path / "recovery-state"
    state.mkdir(mode=0o700)
    w.operation.state = state
    w.operation.keys = {
        "operator": identities["operator"][1],
    }
    decision = w.operation.prepare()["decision"]
    envelope = signed_recovery(decision, identities)
    return w, decision, envelope, state


def test_signed_recovery_creates_lkg_without_deployment(
    recovery_world, identities, tmp_path
):
    w, decision, envelope, state = configure_execution(
        recovery_world, identities, tmp_path
    )
    original = journal_bytes(w.world.directory)
    docker_calls = list(w.world.world.calls)

    result = w.operation.execute(envelope)

    assert result["status"] == "succeeded"
    assert result["lastGoodCreated"] is True
    assert result["executorStarted"] is False
    assert result["bootstrapReplayed"] is False
    assert w.world.world.calls == docker_calls

    audit = state / (
        "first-rollout-recovery-" + decision["id"]
    )
    assert (audit / "original.sqlite3").read_bytes() == original
    assert (audit / "approval.json").read_bytes() == envelope

    with w.world.lock() as lease:
        with Journal(w.world.directory, lease) as journal:
            good = journal.last_good("project-router", "web")
            assert good is not None
            assert good["run"] == decision["run"]
            assert good["target"] == decision["image"]
            assert not journal.incomplete()
            assert not journal.authorization_recovery()
            assert journal.production_bootstrap(
                w.candidate.host, w.candidate.daemon
            )

    # Fail closed after success; no replay or second journal mutation.
    committed = journal_bytes(w.world.directory)
    with pytest.raises(DockerError):
        w.operation.execute(envelope)
    assert journal_bytes(w.world.directory) == committed


@pytest.mark.parametrize("field", [
    "run",
    "image",
    "candidate",
    "journal_sha256",
    "observation_sha256",
    "inventory_sha256",
])
def test_signed_recovery_rejects_changed_facts(
    recovery_world, identities, tmp_path, field
):
    w, decision, _, state = configure_execution(
        recovery_world, identities, tmp_path
    )
    original = journal_bytes(w.world.directory)
    changed = dict(decision)

    value = changed[field]
    changed[field] = (
        ("0" if value[0] != "0" else "1") + value[1:]
    )
    envelope = signed_recovery(changed, identities)

    with pytest.raises(DockerError):
        w.operation.execute(envelope)

    assert journal_bytes(w.world.directory) == original
    assert not list(state.iterdir())


def test_recovery_sql_failure_rolls_back_atomically(
    recovery_world, identities, tmp_path, monkeypatch
):
    w, decision, envelope, state = configure_execution(
        recovery_world, identities, tmp_path
    )
    original = journal_bytes(w.world.directory)
    validate = Journal.validate

    def fail_after_updates(journal):
        validate(journal)
        result = journal.db.execute(
            """
            SELECT status FROM runs
            WHERE application='project-router'
            """
        ).fetchone()
        if result is not None and result[0] == "succeeded":
            raise RuntimeError("simulated commit validation failure")

    monkeypatch.setattr(Journal, "validate", fail_after_updates)

    with pytest.raises(RuntimeError, match="simulated"):
        w.operation.execute(envelope)

    assert journal_bytes(w.world.directory) == original
    assert (
        state / ("first-rollout-recovery-" + decision["id"])
        / "original.sqlite3"
    ).read_bytes() == original

    monkeypatch.setattr(Journal, "validate", validate)
    assert w.operation.execute(envelope)["lastGoodCreated"] is True


from scripts.cd.rollout_recovery import recovery_public_acceptance
from scripts.cd.policy import StandingPolicy
from scripts.cd.release import ImageRelease
from scripts.production_integration.public_postconditions import (
    PublicPolicy,
    PublicPostconditions,
)
from scripts.production_integration.public_witness import (
    SignedPublicWitness,
)
from test_cd import policy_document, signed_policy


@pytest.fixture
def pinned_public_world(recovery_world, identities):
    w = recovery_world
    doc = policy_document(identities)
    doc["host"] = w.candidate.host
    doc["daemon"] = w.candidate.daemon

    release = ImageRelease(
        source_sha=w.candidate.producer_sha,
        image=w.candidate.image,
        html_sha256="d" * 64,
        run_id="1",
        run_attempt="1",
    )
    doc["application"]["initial_release"] = asdict(release)

    standing = signed_policy(doc, identities)

    public = PublicPolicy(
        identity=(w.candidate.host, w.candidate.daemon),
        ipv4=("1.1.1.1",),
        ipv6=(),
        resolver="1.1.1.1",
        ca_file="/tmp/offline-recovery-ca.pem",
        ca_sha256="e" * 64,
        body_sha256="f" * 64,
    )

    w.installation.files = (
        (public.ca_file, public.ca_sha256),
    )

    return SimpleNamespace(
        candidate=w.candidate,
        app=w.world.world.app,
        standing=standing,
        installation=w.installation,
        public=public,
        release=release,
    )


def test_signed_initial_release_pins_public_acceptance(
    pinned_public_world,
):
    w = pinned_public_world

    result = recovery_public_acceptance(
        w.candidate,
        w.app,
        standing=w.standing,
        installation=w.installation,
        installed_public=w.public,
    )

    assert type(result) is PublicPostconditions
    assert type(result.external) is SignedPublicWitness
    assert result.policy.body_sha256 == w.release.html_sha256
    assert result.policy.body_sha256 != w.public.body_sha256
    assert result.policy.ca_sha256 == w.public.ca_sha256
    assert result.external.public_key == (
        w.standing.document["observer"]["key"]
    )


@pytest.mark.parametrize("change", [
    "image",
    "producer",
    "manager",
    "host",
    "ca-pin",
    "application-image",
])
def test_public_acceptance_rejects_unbound_authority(
    pinned_public_world, change,
):
    w = pinned_public_world
    candidate = w.candidate
    public = w.public
    app = w.app

    if change == "image":
        candidate = replace(
            candidate,
            image=(
                "ghcr.io/danilfaritovich/project-router@sha256:"
                + "0" * 64
            ),
        )
    elif change == "producer":
        candidate = replace(candidate, producer_sha="0" * 40)
    elif change == "manager":
        candidate = replace(candidate, manager_sha="0" * 40)
    elif change == "host":
        candidate = replace(candidate, host="0" * 32)
    elif change == "ca-pin":
        public = replace(public, ca_sha256="0" * 64)
    elif change == "application-image":
        spec = app.spec
        spec["components"][0]["image"] = (
            "ghcr.io/danilfaritovich/project-router@sha256:"
            + "0" * 64
        )
        app = replace(app, spec_json=encode(spec))

    with pytest.raises(DockerError):
        recovery_public_acceptance(
            candidate,
            app,
            standing=w.standing,
            installation=w.installation,
            installed_public=public,
        )


from scripts.cd.rollout_recovery import recovery_runtime_pins
from scripts.cd.host import StandingReview
from scripts.production_integration.host_policy import HostPolicy
from scripts.production_integration.local_sources import PATHS


@pytest.fixture
def signed_runtime_world(recovery_world, identities):
    w = recovery_world
    doc = policy_document(identities)
    doc["host"] = w.candidate.host
    doc["daemon"] = w.candidate.daemon
    doc["application"]["initial_release"] = asdict(
        ImageRelease(
            w.candidate.producer_sha,
            w.candidate.image,
            "f" * 64,
            "1",
            "1",
        )
    )

    host = HostPolicy(
        host=w.candidate.host,
        daemon=w.candidate.daemon,
        network_hash="sha256:" + "a" * 64,
        resources_hash="sha256:" + "b" * 64,
        storage_hash="sha256:" + "c" * 64,
        minimum_bytes=2**30,
        minimum_inodes=100,
        backup_restore="reviewed-backup",
        console_recovery="reviewed-console",
        network_allocation="reviewed-network",
        storage_budget="reviewed-storage",
        containment="retain-and-investigate",
    )

    from dataclasses import asdict as fields

    doc["host_contract"] = {
        "host_policy": fields(host),
        "kernel_sha256": "sha256:" + "d" * 64,
        "storage_roots": {path: {} for path in PATHS},
        "caddyfile_sha256": "e" * 64,
        "caddy_runtime_hash": "sha256:" + "f" * 64,
        "caddy_evidence_hash": "sha256:" + "1" * 64,
        "rollout": {
            "container_id": "a" * 64,
            "proxy_id": "b" * 64,
            "private_id": "c" * 64,
            "infrastructure": [
                ["container", "a" * 64,
                 "portfolio-gateway-caddy-1", "sha256:" + "1" * 64],
                ["network", "b" * 64,
                 "portfolio-proxy", "sha256:" + "2" * 64],
                ["network", "c" * 64,
                 "portfolio-project-router-private",
                 "sha256:" + "3" * 64],
            ],
        },
    }

    def reviewed(document):
        standing = signed_policy(document, identities)
        return StandingReview(w.installation, standing)

    return SimpleNamespace(
        candidate=w.candidate,
        app=w.world.world.app,
        document=doc,
        reviewed=reviewed,
    )


def test_signed_runtime_pins_are_accepted(signed_runtime_world):
    w = signed_runtime_world

    pins = recovery_runtime_pins(
        w.candidate, w.app, w.reviewed(w.document)
    )

    assert pins["container_id"] == "a" * 64
    assert pins["proxy_id"] == "b" * 64
    assert pins["private_id"] == "c" * 64
    assert len(pins["infrastructure"]) == 3
    assert all(
        type(row) is tuple for row in pins["infrastructure"]
    )


@pytest.mark.parametrize("failure", [
    "wrong-host",
    "wrong-manager",
    "wrong-image",
    "wrong-producer",
    "missing-network",
    "extra-container",
])
def test_unsigned_or_foreign_runtime_is_rejected(
    signed_runtime_world, failure
):
    w = signed_runtime_world
    candidate = w.candidate
    document = w.document

    if failure == "wrong-host":
        candidate = replace(candidate, host="0" * 32)
    elif failure == "wrong-manager":
        candidate = replace(candidate, manager_sha="0" * 40)
    elif failure == "wrong-image":
        candidate = replace(
            candidate,
            image="ghcr.io/danilfaritovich/project-router@sha256:"
                  + "0" * 64,
        )
    elif failure == "wrong-producer":
        candidate = replace(candidate, producer_sha="0" * 40)
    elif failure == "missing-network":
        document["host_contract"]["rollout"]["infrastructure"].pop()
    elif failure == "extra-container":
        document["host_contract"]["rollout"]["infrastructure"].append(
            ["container", "d" * 64,
             "foreign-container", "sha256:" + "4" * 64]
        )

    with pytest.raises(DockerError):
        recovery_runtime_pins(
            candidate, w.app, w.reviewed(document)
        )


@pytest.mark.parametrize("outcome", [
    "missing",
    "denied",
    "wrong",
])
def test_public_https_must_be_verified(
    failed_rollout, outcome
):
    world, candidate = failed_rollout
    original = journal_bytes(world.directory)

    public = (
        None if outcome == "missing"
        else OfflinePublic(outcome)
    )
    clock = Clock()

    with pytest.raises(DockerError):
        observe_live_release(
            candidate,
            world.world.app,
            world.world.provider,
            Probes(),
            POLICY,
            public_postconditions=public,
            clock=clock.now,
            sleep=clock.sleep,
        )

    assert journal_bytes(world.directory) == original


from scripts.cd.rollout_recovery import build_recovery_state_reader
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import (
    ProductionStateProvider, ProductionStateReader,
)
from scripts.production_integration.runtime import (
    CaddyRuntime, RuntimePaths,
)
from scripts.production_integration.runtime_sources import (
    RuntimeSource, GatewaySource, ReadinessSource,
)
from test_deployment import image as fake_image


@pytest.fixture
def recovery_reader_world(signed_runtime_world):
    w = signed_runtime_world

    caddy = CaddyRuntime(
        "ghcr.io/danilfaritovich/portfolio-caddy@sha256:"
        + "9" * 64,
        "2.11.7",
        RuntimePaths(
            "/srv/portfolio/state",
            "/srv/portfolio/locks",
            "/srv/portfolio/journal",
            "/srv/portfolio/caddy-config",
            "/srv/portfolio/certificates",
            "/srv/portfolio/caddy-data",
            1024 * 1024,
        ),
    )
    caddy_image = fake_image(caddy.image)

    w.document["host_contract"]["caddy_runtime_hash"] = hashed(
        caddy.render().model
    )
    w.document["host_contract"]["caddy_evidence_hash"] = hashed(
        asdict(caddy_image)
    )

    review = w.reviewed(w.document)
    installation = review.installation

    public = PublicPolicy(
        identity=(w.candidate.host, w.candidate.daemon),
        ipv4=("1.1.1.1",),
        ipv6=(),
        resolver="1.1.1.1",
        ca_file="/tmp/offline-recovery-ca.pem",
        ca_sha256="a" * 64,
        body_sha256=(
            w.document["application"]["initial_release"]["html_sha256"]
        ),
    )
    witness = SignedPublicWitness(
        installation,
        review.standing.document["observer"]["key"],
    )
    public_check = PublicPostconditions(public, external=witness)

    def build(**changes):
        args = {
            "installation": installation,
            "standing_review": review,
            "candidate": w.candidate,
            "app": w.app,
            "caddy": caddy,
            "caddy_image": caddy_image,
            "proxy_subnet": "172.30.240.0/24",
            "proxy_gateway": "172.30.240.1",
            "private_subnet": "172.30.241.0/24",
            "resolve_image": lambda reference, inspected: fake_image(
                reference
            ),
            "public_postconditions": public_check,
        }
        args.update(changes)
        return build_recovery_state_reader(**args)

    return SimpleNamespace(
        build=build,
        candidate=w.candidate,
        caddy_image=caddy_image,
        public_check=public_check,
    )


def test_recovery_reader_uses_real_read_only_source_types(
    recovery_reader_world,
):
    provider, probes = recovery_reader_world.build()

    assert type(provider) is ProductionStateProvider
    assert type(provider.reader) is ProductionStateReader

    ports = provider.reader.ports
    assert type(ports["runtime"]) is RuntimeSource
    assert type(ports["gateway"]) is GatewaySource
    assert type(ports["readiness"]) is ReadinessSource
    from scripts.cd.application import ActiveRoutes
    assert type(ports["gateway"].postconditions) is ActiveRoutes
    assert provider.reader.source_trust.attestor is not None
    assert probes.network_id == "c" * 64

    # Construction performs no Docker/SSH/HTTP reads or writes.
    assert provider.sample is None


@pytest.mark.parametrize("failure", [
    "wrong-manager",
    "unverified-caddy",
    "missing-public-authority",
    "wrong-proxy",
])
def test_recovery_reader_rejects_untrusted_wiring(
    recovery_reader_world, failure,
):
    w = recovery_reader_world
    args = {}

    if failure == "wrong-manager":
        args["candidate"] = replace(
            w.candidate, manager_sha="0" * 40
        )
    elif failure == "unverified-caddy":
        args["caddy_image"] = replace(
            w.caddy_image, verified=False
        )
    elif failure == "missing-public-authority":
        args["public_postconditions"] = OfflinePublic()
    elif failure == "wrong-proxy":
        args["proxy_subnet"] = "not-a-subnet"

    with pytest.raises((DockerError, ValueError)):
        w.build(**args)


def test_recovery_reader_rejects_missing_source_trust(
    recovery_reader_world, monkeypatch
):
    w = recovery_reader_world
    provider, _ = w.build()
    reader = provider.reader

    monkeypatch.setattr(
        reader.docker,
        "identity",
        lambda: (w.candidate.host, w.candidate.daemon),
    )
    reader.source_trust = None

    with pytest.raises(
        DockerError,
        match="production-source-trust-unconfigured",
    ):
        provider.refresh()

    assert provider.sample is None


def test_recovery_reader_propagates_runtime_failure(
    recovery_reader_world, failed_rollout, monkeypatch
):
    w = recovery_reader_world
    world, _ = failed_rollout
    provider, _ = w.build()
    reader = provider.reader

    monkeypatch.setattr(
        reader.docker,
        "identity",
        lambda: (w.candidate.host, w.candidate.daemon),
    )

    reader.desired_reader = lambda: DesiredState(
        w.candidate.manager_sha,
        (world.world.app,),
    )

    def fail_runtime(*args, **kwargs):
        raise DockerError("synthetic-runtime-read-failed")

    monkeypatch.setattr(
        reader.ports["runtime"],
        "collect_round",
        fail_runtime,
    )

    original = journal_bytes(world.directory)

    with pytest.raises(
        DockerError,
        match="synthetic-runtime-read-failed",
    ):
        provider.refresh()

    assert provider.sample is None
    assert journal_bytes(world.directory) == original


def test_recovery_reader_rejects_fabricated_source_proof(
    recovery_reader_world
):
    from scripts.production_integration.observations import SourceResult

    w = recovery_reader_world
    provider, _ = w.build()
    reader = provider.reader
    runtime = reader.ports["runtime"]

    forged = SourceResult(
        runtime.identity,
        "synthetic-round",
        0,
        True,
        True,
        {},
    )

    with pytest.raises(
        DockerError,
        match="production-local-source-channel-unbound",
    ):
        reader.source_trust.verify(
            "runtime",
            (w.candidate.host, w.candidate.daemon),
            runtime,
            forged,
        )


def test_recovery_docker_port_rejects_mutation(
    recovery_reader_world
):
    provider, _ = recovery_reader_world.build()

    with pytest.raises(
        DockerError,
        match="stage-09-4-operation-authorization-unavailable",
    ):
        provider.reader.docker.up(None, "web")


@pytest.mark.parametrize('document', [
    {'op': 'prepare', 'policy': {}},
    {'op': 'execute', 'envelope': {}},
    {'op': 'execute', 'envelope': 'not-base64'},
    {'op': 'reset'},
])
def test_root_entry_rejects_request_before_installed_wiring(monkeypatch, document):
    import io
    import json
    import sys
    from scripts.cd import bridge, rollout_recovery

    monkeypatch.setattr('os.geteuid', lambda: 0)
    from scripts.production_integration.operator_approval import canonical
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(canonical(document))))
    def unexpected():
        pytest.fail('invalid request reached installed wiring')
    monkeypatch.setattr(bridge, 'installation', unexpected)
    with pytest.raises((DockerError, ValueError)):
        rollout_recovery.main()


def test_root_entry_uses_signed_recovery_and_retains_history(recovery_world, identities, tmp_path, monkeypatch, capsys):
    import base64
    import io
    import json
    import os
    import sys
    from scripts.cd import application, bridge, rollout_recovery
    from scripts.cd.signatures import sign

    w = recovery_world
    w.operation.keys = {'operator': identities['operator'][1]}
    w.operation.state = tmp_path / 'audit'
    w.operation.state.mkdir(mode=0o700)
    decision = w.operation.prepare()['decision']
    envelope = sign(decision, principal='operator', key=identities['operator'][0],
                    namespace=rollout_recovery.RECOVERY_NAMESPACE)
    original = journal_bytes(w.world.directory)
    real_geteuid = os.geteuid
    monkeypatch.setattr('os.geteuid', lambda: 0)
    monkeypatch.setattr(bridge, 'installation', lambda: w.installation)
    monkeypatch.setattr(application, 'installed_policy', lambda installed: w.policy)
    fixed = object()
    monkeypatch.setitem(sys.modules, 'cd_bindings', SimpleNamespace(POLICY=fixed))
    def installed(installed, standing, policy):
        assert installed is w.installation and standing is w.policy and policy is fixed
        # Only the entry gate is root-simulated; fixture files belong to this user.
        monkeypatch.setattr(os, 'geteuid', real_geteuid)
        return w.operation
    monkeypatch.setattr(rollout_recovery, 'installed_recovery', installed)
    request = {'op': 'execute', 'envelope': base64.b64encode(envelope).decode()}
    from scripts.production_integration.operator_approval import canonical
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(canonical(request))))
    assert rollout_recovery.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'succeeded'
    assert (w.operation.state / ('first-rollout-recovery-' + decision['id']) / 'original.sqlite3').read_bytes() == original
    with w.world.lock() as lease, Journal(w.world.directory, lease) as journal:
        assert journal.last_good('project-router', 'web')['run'] == decision['run']
        assert not journal.incomplete() and not journal.authorization_recovery()


def test_public_challenge_is_published_after_runtime_verification(failed_rollout):
    world, candidate = failed_rollout
    clock = Clock()
    order = []
    class Public(OfflinePublic):
        def verify(self, routes, identity):
            assert order == ['challenge']
            order.append('public')
            return super().verify(routes, identity)
    observe_live_release(candidate, world.world.app, world.world.provider, Probes(), POLICY,
        public_postconditions=Public(), before_public=lambda: order.append('challenge'),
        clock=clock.now, sleep=clock.sleep)
    assert order == ['challenge', 'public']
