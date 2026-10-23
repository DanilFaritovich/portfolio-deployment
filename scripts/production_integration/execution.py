"""One-shot transaction coordinator over the Stage 05/06 engine, never a daemon."""
from contextlib import contextmanager
from dataclasses import dataclass
import time
import re

from scripts.deployment import execute
from scripts.deployment_journal import Intent, Journal
from scripts.docker_adapter import DockerError
from scripts.production_integration.artifacts import SourceRequest, SourceVerification
from scripts.production_integration.authorization import review_approval
from scripts.production_integration.gateway import RehearsalLifecycle
from scripts.production_integration.locking import production_host_lock, require_production_lease
from scripts.production_integration.release import MANAGER, PRODUCER, ORIGIN


class ExecutionGrant:
    """In-process lifetime only. Installed code is trusted, request data is not."""
    def __init__(self, candidate, lease, journal, claims):
        self.candidate, self.lease, self.journal, self.claims = candidate, lease, journal, claims
        self.active = True

    def require(self):
        require_production_lease(self.lease)
        if not self.active or self.journal.lease is not self.lease:
            raise DockerError('production-execution-grant-inactive')
        row = self.journal.db.execute('SELECT candidate,state FROM authorizations WHERE nonce=?', (self.claims.nonce,)).fetchone()
        if row is None or tuple(row) != (self.candidate.identity, 'consumed'):
            raise DockerError('production-execution-grant-unconsumed-required')


class ProductionLifecycle(RehearsalLifecycle):
    """Same step/rollback/readiness/gateway/LKG logic; distinct authenticated open."""
    def __init__(self, journal, grant, probes, *, gateway, **kwargs):
        super().__init__(journal.directory, probes, gateway=gateway, run_id=grant.claims.nonce, **kwargs)
        self.production_journal, self.grant = journal, grant

    @contextmanager
    def open(self, lease, adapter, provider):
        self.grant.require()
        if lease is not self.grant.lease or adapter.test_session is not None:
            raise DockerError('production-lifecycle-context-mismatch')
        self.adapter, self.provider, self.journal = adapter, provider, self.production_journal
        try:
            yield self
        finally:
            self.journal = None


@dataclass(frozen=True)
class ProductionResult:
    status: str
    candidate: str
    reasons: tuple[str, ...]
    results: tuple = ()


class ProductionExecutor:
    """Dependencies come exclusively from sealed installation composition.

    installation.verify validates root/isolated code/tool/dependency pins BEFORE
    authenticating request data. No network/Engine access happens in constructors.
    release_factory supplies a fresh existing ReleaseVerifier for the exact candidate.
    adapter_factory supplies the fixed production adapter, bound to a live grant.
    """
    def __init__(self, *, installation, preflight, journal_directory, approval_verifier=None,
                 release_factory=None, adapter_factory=None, probes=None, gateway=None,
                 bootstrap=None, lifecycle_options=None, clock=time.time):
        self.installation, self.preflight, self.directory = installation, preflight, journal_directory
        self.verifier, self.release_factory, self.adapter_factory = approval_verifier, release_factory, adapter_factory
        self.probes, self.gateway, self.bootstrap = probes, gateway, bootstrap
        self.lifecycle_options, self.clock = lifecycle_options or {}, clock

    def run(self, candidate, envelope):
        grant = None
        consumed = False
        identity = ''
        try:
            self.installation.verify()
            identity = candidate.identity
            if candidate.manager_sha != self.installation.manager_sha:
                raise DockerError('production-installed-manager-sha-mismatch')
            claims = review_approval(candidate, envelope, verifier=self.verifier, now=self.clock())
            if self.release_factory is None:
                raise DockerError('production-release-verifier-unavailable')
            request = SourceRequest(PRODUCER, candidate.producer_sha, candidate.manager_sha, candidate.image, ORIGIN, MANAGER)
            proof = self.release_factory(candidate).verify(request)
            if (not isinstance(proof, SourceVerification) or proof.request != request or proof.blockers or
                not isinstance(proof.evidence_identity, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', proof.evidence_identity)):
                raise DockerError('production-release-unverified')
            if candidate.scope == 'first-bootstrap':
                if self.bootstrap is None or self.bootstrap.port is None:
                    raise DockerError('production-bootstrap-executor-unavailable')
            elif any(x is None for x in (self.adapter_factory, self.probes, self.gateway)):
                raise DockerError('production-runtime-dependencies-unavailable')
            with production_host_lock() as lease:
                self.installation.verify()
                fresh = self.preflight.prepare(candidate.scope)
                if fresh.candidate != candidate:
                    raise DockerError('production-exact-candidate-changed')
                # Re-authenticate after potentially slow release/lock/observations.
                claims = review_approval(candidate, envelope, verifier=self.verifier, now=self.clock())
                with Journal(self.directory, lease) as journal:
                    if candidate.scope == 'first-project-router-rollout' and not journal.production_bootstrap(candidate.host, candidate.daemon):
                        raise DockerError('production-bootstrap-journal-baseline-required')
                    if candidate.scope == 'first-bootstrap' and journal.last_good('manager-bootstrap', 'gateway') is not None:
                        raise DockerError('production-first-bootstrap-already-recorded')
                    journal.consume_authorization(claims, candidate)
                    consumed = True
                    grant = ExecutionGrant(candidate, lease, journal, claims)
                    try:
                        if candidate.scope == 'first-bootstrap':
                            self.bootstrap.execute(fresh, grant)
                            results, success = (), True
                        else:
                            # Durable bootstrap success is necessary but never
                            # replaces fresh independently attested runtime evidence.
                            if journal.last_good('manager-bootstrap', 'gateway') is None:
                                raise DockerError('production-bootstrap-journal-baseline-required')
                            adapter = self.adapter_factory(grant)
                            gateway = (self.gateway.for_operation(grant, fresh) if hasattr(self.gateway, 'for_operation') else self.gateway)
                            lifecycle = ProductionLifecycle(journal, grant, self.probes, gateway=gateway,
                                                            **self.lifecycle_options)
                            results = execute(fresh.desired, fresh.observed, fresh.plan, 'project-router', adapter,
                                              self.preflight.provider, lifecycle=lifecycle, lease=lease)
                            success = bool(results) and all(r.release_success for r in results)
                        state = 'succeeded' if success else ('recovery-required' if journal.incomplete() else 'blocked')
                        journal.finish_authorization(claims.nonce, state)
                        return ProductionResult(state, identity, () if success else ('production-execution-incomplete',), results)
                    except Exception:
                        # Ambiguous exceptions are never classified as safe retry.
                        journal.finish_authorization(claims.nonce, 'recovery-required')
                        return ProductionResult('recovery-required', identity, ('production-execution-outcome-unverified',))
                    finally:
                        grant.active = False
        except DockerError as error:
            # Codes are closed installation/engine diagnostics, never raw output.
            return ProductionResult('recovery-required' if consumed else 'blocked', identity, (error.code,))
        except Exception:
            return ProductionResult('recovery-required' if consumed else 'blocked', identity, ('production-execution-dependency-unavailable',))


class BootstrapExecution:
    """Ordered manager-only actions, journaled in the SAME Stage 06 journal.

    port is the independently installed fixed resource adapter. It must validate
    exact-image Caddy provenance/runtime evidence, all storage and empty config
    before returning from preflight, and attest two bounded successful checks
    of returned IDs/IPAM/ownership/runtime/storage after creation. No cleanup API.
    """
    def __init__(self, port=None):
        self.port = port

    def execute(self, prepared, grant):
        grant.require()
        if self.port is None:
            raise DockerError('production-bootstrap-resource-attestor-unavailable')
        c, journal, run = grant.candidate, grant.journal, grant.claims.nonce
        self.port.preflight(prepared, grant)
        journal.prepare(run, 'manager-bootstrap', c.manager_sha,
                        (Intent('gateway', None, c.image, c.runtime_hash.removeprefix('sha256:')),))
        journal.transition(run, 'gateway', 'applying', 'mutation-started')
        try:
            for action in prepared.bootstrap.operations:
                grant.require()
                self.port.apply(action, prepared, grant)
            journal.transition(run, 'gateway', 'verifying', 'mutation-completed')
            evidence = self.port.verify(prepared, grant)
            # Journal enforces >=2 actual successful bounded verification probes.
            journal.transition(run, 'gateway', 'succeeded', 'readiness-verified', evidence)
            journal.promote(run)
        except BaseException:
            journal.fail_run(run)
            raise
