"""Controlled installed adapters, real Stage 05/06 engine, flock and SQLite."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import os
import socket
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from scripts.composite_state import CompositeSample, CompositeStateProvider, SourceEvidence
from scripts.deployment import host_lock, owns_host_lock
from scripts.deployment_journal import Journal, JournalError, LEGACY_SCHEMA
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import DockerObservation
from scripts.planning_inputs import load_observed_state
from scripts.production_integration.authorization import ApprovalClaims
from scripts.production_integration.bootstrap import BootstrapInput, BootstrapNetworkContract
from scripts.production_integration.candidate import CandidatePreflight, HostContracts, hashed
from scripts.production_integration.execution import BootstrapExecution, ProductionExecutor
from scripts.production_integration.artifacts import SourceVerification
from scripts.production_integration.runtime import CaddyRuntime
from scripts.reconciliation import DesiredState, encode
from test_planning import observation, empty
from test_production_contracts import registration, REVISION
from test_production_integration import router, state, proxy, paths
from test_readiness_recovery import Clock, Probes, POLICY, VERIFIED


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('production execution test attempted live I/O')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


class ControlledInstallation:
    manager_sha = REVISION
    def verify(self): pass


class ControlledHostAttestor:
    def verify(self, contracts, identity): return contracts


class ControlledIssuer:
    """Test-only authenticated channel: unknown/unsigned envelopes never pass."""
    def __init__(self): self.envelopes = {}
    def authenticate(self, envelope):
        if type(envelope) is not bytes or envelope not in self.envelopes:
            raise DockerError('production-test-signature-invalid')
        return self.envelopes[envelope]


class World:
    def __init__(self, app):
        self.app, self.present, self.route, self.calls = app, False, False, []
        self.desired = DesiredState(REVISION, (app,))
        artifact, container, image = state(app)
        self.container, self.image = container, image
        _, self.proxy = proxy()
        self.foreign = ()
        self.provider = CompositeStateProvider(self.read, clock=lambda: 0)

    def read(self):
        doc = observation(self.desired) if self.present else empty()
        if self.present and not self.route:
            doc['applications'][0]['route'].update(existence='absent', configuration=None)
        resources = (self.proxy, *self.foreign, *((self.container,) if self.present else ()))
        docker = DockerObservation(resources, (self.image,), True)
        sources = tuple(SourceEvidence(s, 'round', 0, True, True) for s in ('docker', 'runtime', 'gateway', 'readiness'))
        return CompositeSample(self.desired, load_observed_state(doc), docker, sources, self.route)

    def adapter(self, grant):
        world = self
        class Adapter:
            test_session = None
            def authorize(self, artifact): grant.require()
            def up(self, artifact, service):
                grant.require()
                world.calls.append(('up', artifact.project, service))
                world.present = True
        return Adapter()

    def activate(self, *args, **kwargs):
        self.calls.append(('gateway',))
        self.route = True


@pytest.fixture
def setup(router, tmp_path, monkeypatch):
    from scripts.production_integration import execution
    world, issuer, clock = World(router), ControlledIssuer(), Clock()
    lock = tmp_path / 'locks'
    @contextmanager
    def controlled_lock():
        with host_lock(lock) as lease: yield lease
    def require_lease(lease):
        if not owns_host_lock(lease): raise DockerError('production-test-lock-invalid')
    monkeypatch.setattr(execution, 'production_host_lock', controlled_lock)
    monkeypatch.setattr(execution, 'require_production_lease', require_lease)
    contracts = HostContracts('a'*32, 'daemon', 0,
                             encode({'hostRoutes': [], 'approvedSubnet': '172.30.0.0/24'}),
                             encode({'paths': asdict(paths()), 'availableBytes': 2**30}),
                             encode({'unfinished': [], 'containment': 'retain-and-investigate'}), True)
    preflight = CandidatePreflight(manager_sha=REVISION, producer_sha='b'*40,
        image=router.spec['components'][0]['image'], content_reader=lambda:(world.desired, b'manifest', b'policy'),
        engine=SimpleNamespace(identity=lambda:('a'*32, 'daemon')),
        contracts=SimpleNamespace(collect=lambda:contracts), attestor=ControlledHostAttestor(),
        provider=world.provider, clock=lambda:0)
    def release(candidate):
        return SimpleNamespace(verify=lambda request: SourceVerification(request, (), hashed(asdict(candidate))))
    executor = ProductionExecutor(installation=ControlledInstallation(), preflight=preflight,
        journal_directory=tmp_path/'journal', approval_verifier=issuer, release_factory=release,
        adapter_factory=world.adapter, probes=Probes(), gateway=world,
        lifecycle_options={'policy': POLICY, 'clock': clock.now, 'sleep': clock.sleep}, clock=lambda:100)
    # Genuine Stage06 bootstrap baseline, not a file/boolean interpreted by code.
    with controlled_lock() as lease, Journal(tmp_path/'journal', lease) as journal:
        from scripts.deployment_journal import Intent
        from test_production_preparation import candidate
        bootstrap_candidate = replace(candidate(), manager_sha=REVISION, scope='first-bootstrap')
        journal.consume_authorization(ApprovalClaims(bootstrap_candidate.identity, 'operator', 90, 150, '0'*32), bootstrap_candidate)
        journal.prepare('0'*32, 'manager-bootstrap', REVISION,
                        (Intent('gateway', None, preflight.image, hashed({'bootstrap': 'verified'})[7:]),))
        journal.transition('0'*32, 'gateway', 'verifying', 'mutation-completed')
        journal.transition('0'*32, 'gateway', 'succeeded', 'readiness-verified', VERIFIED)
        journal.promote('0'*32)
        journal.finish_authorization('0'*32, 'succeeded')
    return SimpleNamespace(world=world, issuer=issuer, executor=executor, preflight=preflight,
                           lock=controlled_lock, directory=tmp_path/'journal', lock_path=lock, contracts=contracts)


def approve(s, candidate=None, nonce='1'*32, **changes):
    c = candidate or s.preflight.prepare('first-project-router-rollout').candidate
    claims = replace(ApprovalClaims(c.identity, 'trusted-operator', 90, 150, nonce), **changes)
    envelope = ('independently-signed-' + nonce).encode()
    s.issuer.envelopes[envelope] = claims
    return c, envelope


def test_real_engine_lifecycle_uses_one_lease_and_promotes_whole_app(setup):
    s = setup
    c, envelope = approve(s)
    result = s.executor.run(c, envelope)
    assert result.status == 'succeeded', result
    assert s.world.calls == [('up', 'portfolio-project-router', 'web'), ('gateway',)]
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web')['run'] == '1'*32
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?", ('1'*32,)).fetchone()[0] == 'succeeded'
    # Process restart/new executor cannot replay even if observations are restored.
    s.world.present, s.world.route = False, False
    assert s.executor.run(c, envelope).status == 'blocked'
    _, another = approve(s, c, nonce='2'*32)
    assert s.executor.run(c, another).status == 'blocked'
    assert len(s.world.calls) == 2


@pytest.mark.parametrize('case', ['expired', 'future', 'long-window', 'wrong-candidate', 'wrong-host',
                                 'unsigned', 'verifier', 'manager', 'release', 'changed-policy', 'changed-manifest',
                                 'changed-runtime', 'changed-storage', 'no-attestor', 'untrusted', 'incomplete',
                                 'daemon', 'lock', 'bootstrap-baseline'])
def test_blockers_cannot_mutate(setup, case):
    s = setup
    c, envelope = approve(s)
    if case in ('expired', 'future', 'long-window', 'wrong-candidate'):
        changes = {'expired':{'expires':100}, 'future':{'not_before':101},
                   'long-window':{'expires':4000}, 'wrong-candidate':{'candidate':hashed({'other':True})}}[case]
        s.issuer.envelopes[envelope] = replace(s.issuer.envelopes[envelope], **changes)
    elif case == 'wrong-host': c = replace(c, host='b'*32)
    elif case == 'unsigned': envelope = {'verified':True}
    elif case == 'verifier': s.executor.verifier = None
    elif case == 'manager': c = replace(c, manager_sha='c'*40)
    elif case == 'release': s.executor.release_factory = None
    elif case in ('changed-policy', 'changed-manifest'):
        s.preflight.content_reader = lambda:(s.world.desired, b'changed' if case.endswith('manifest') else b'manifest',
                                            b'changed' if case.endswith('policy') else b'policy')
    elif case == 'changed-runtime': s.world.present = True
    elif case == 'changed-storage':
        s.preflight.contracts.collect = lambda:replace(s.contracts, storage=encode({'capacity':1}))
    elif case == 'no-attestor': s.preflight.attestor = None
    elif case in ('untrusted', 'incomplete'):
        reader = s.world.provider.reader
        s.world.provider.reader = lambda:replace(reader(), sources=tuple(
            replace(e, trusted=False) if case == 'untrusted' else replace(e, complete=False) for e in reader().sources))
    elif case == 'daemon': s.preflight.engine.identity = lambda:('a'*32, 'foreign')
    elif case == 'bootstrap-baseline':
        with s.lock() as lease, Journal(s.directory, lease) as j:
            j.db.execute('DELETE FROM good')
            j.db.execute('DELETE FROM events'); j.db.execute('DELETE FROM attempts'); j.db.execute('DELETE FROM runs')
    elif case == 'lock':
        with s.lock():
            assert s.executor.run(c,envelope).status == 'blocked'
        assert not s.world.calls
        return
    result = s.executor.run(c, envelope)
    assert result.status == 'blocked', result
    assert not s.world.calls


@pytest.mark.parametrize('point', ['consumed', 'intent', 'up', 'after-up'])
def test_process_death_never_reopens_authority(setup, monkeypatch, point):
    s = setup
    c, envelope = approve(s)
    from scripts.production_integration.execution import ExecutionGrant
    original_init = ExecutionGrant.__init__
    original_prepare, original_adapter = Journal.prepare, s.executor.adapter_factory
    def crash(*args, **kwargs): raise SystemExit('process lost')
    if point == 'consumed': monkeypatch.setattr('scripts.production_integration.execution.ExecutionGrant.__init__', crash)
    elif point == 'intent':
        original = Journal.prepare
        def prepare(*args): original(*args); crash()
        monkeypatch.setattr(Journal, 'prepare', prepare)
    else:
        original = s.executor.adapter_factory
        def adapter(grant):
            a = original(grant)
            up = a.up
            def interrupted(*args):
                if point == 'after-up': up(*args)
                crash()
            a.up = interrupted
            return a
        s.executor.adapter_factory = adapter
    with pytest.raises(SystemExit): s.executor.run(c, envelope)
    monkeypatch.setattr('scripts.production_integration.execution.ExecutionGrant.__init__', original_init)
    monkeypatch.setattr(Journal, 'prepare', original_prepare)
    s.executor.adapter_factory = original_adapter
    # No repeated mutation; consumed is permanently blocking even with NEW nonce.
    assert s.executor.run(c, envelope).status == 'blocked'
    _, new = approve(s, c, nonce='2'*32)
    assert s.executor.run(c, new).status == 'blocked'
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?", ('1'*32,)).fetchone()[0] == 'consumed'
        assert journal.authorization_recovery()[0]['decision'] == 'recovery-required'
        assert bool(journal.incomplete()) == (point != 'consumed')
    assert len(s.world.calls) == (1 if point == 'after-up' else 0)


@pytest.mark.parametrize('failure', ['docker', 'unknown', 'caddy', 'readiness', 'storage'])
def test_runtime_failures_preserve_evidence_and_require_recovery(setup, failure):
    s = setup
    c, envelope = approve(s)
    if failure in ('docker', 'unknown'):
        original = s.executor.adapter_factory
        def adapter(grant):
            a = original(grant)
            def up(*args):
                s.world.calls.append(('attempt',))
                raise DockerError('docker-command-failed', failure == 'unknown')
            a.up = up
            return a
        s.executor.adapter_factory = adapter
    elif failure == 'caddy':
        def activate(*args, **kwargs): raise DockerError('caddy-command-failed', True)
        s.world.activate = activate
    elif failure == 'readiness': s.executor.probes = Probes(fail_name='web')
    else:
        original = s.executor.adapter_factory
        def adapter(grant):
            a = original(grant)
            def authorize(*args): raise OSError('storage unavailable')
            a.authorize = authorize
            return a
        s.executor.adapter_factory = adapter
    result = s.executor.run(c, envelope)
    expected = 'blocked' if failure == 'storage' else 'recovery-required'
    assert result.status == expected, result
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web') is None
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?", ('1'*32,)).fetchone()[0] == expected
    assert s.executor.run(c, envelope).status == 'blocked'


def test_simultaneous_operation_and_inode(setup):
    s = setup
    c, envelope = approve(s)
    original = s.executor.adapter_factory
    def adapter(grant):
        assert owns_host_lock(grant.lease)
        inode = os.stat(grant.lease.path).st_ino
        assert s.executor.run(c, envelope).status == 'blocked'
        assert os.stat(grant.lease.path).st_ino == inode
        return original(grant)
    s.executor.adapter_factory = adapter
    assert s.executor.run(c, envelope).status == 'succeeded'


@pytest.mark.parametrize('failure', [None, 'production-storage-content-changed', 'PRIVATE-SENTINEL'])
def test_bootstrap_executes_only_ordered_manager_actions_and_journals(setup, failure):
    s = setup
    with s.lock() as lease, Journal(s.directory, lease) as j:
        for table in ('good', 'events', 'attempts', 'runs', 'authorizations'):
            j.db.execute('DELETE FROM ' + table)
    sample = BootstrapInput('a'*32, 'daemon', 0, True, DockerObservation((),(),True), (), 'absent','absent',())
    s.preflight.bootstrap_reader = SimpleNamespace(collect=lambda:sample)
    s.preflight.bootstrap_attestor = SimpleNamespace(verify=lambda sample,identity:sample)
    s.preflight.proxy = BootstrapNetworkContract('172.30.0.0/24','172.30.0.1')
    s.preflight.private_subnet = '172.31.0.0/24'
    s.preflight.caddy = CaddyRuntime('caddy@sha256:'+'f'*64,'2.11.7',paths())
    prepared = s.preflight.prepare('first-bootstrap')
    actions = []
    class Port:
        def preflight(self, prepared, grant): grant.require()
        def apply(self, action, prepared, grant):
            assert grant.journal.rows()[-1]['state'] == 'applying'
            actions.append(action)
        def verify(self, prepared, grant):
            if failure: raise DockerError(failure)
            return VERIFIED
    s.executor.bootstrap = BootstrapExecution(Port())
    c, envelope = approve(s, prepared.candidate)
    result = s.executor.run(c, envelope)
    if failure:
        assert result.status == 'recovery-required'
        expected = ('production-execution-outcome-unverified',)
        if failure != 'PRIVATE-SENTINEL': expected += (failure,)
        assert result.reasons == expected
        with s.lock() as lease, Journal(s.directory, lease) as journal:
            assert journal.last_good('manager-bootstrap', 'gateway') is None
            assert journal.incomplete()[0]['state'] == 'verifying'
        assert tuple(actions) == prepared.bootstrap.operations
        return
    assert result.status == 'succeeded'
    assert tuple(actions) == prepared.bootstrap.operations
    assert not s.world.calls
    # Exact bootstrap approval cannot authorize rollout.
    rollout = s.preflight.prepare('first-project-router-rollout').candidate
    assert s.executor.run(rollout, envelope).status == 'blocked'


def test_schema_v1_migration_preserves_existing_tables(tmp_path):
    directory = tmp_path/'journal'
    directory.mkdir(mode=0o700)
    db = directory/'journal.sqlite3'
    with sqlite3.connect(db) as connection:
        for sql in LEGACY_SCHEMA: connection.execute(sql)
        connection.execute('PRAGMA user_version=1')
        connection.execute('PRAGMA application_id=1346651185')
    db.chmod(0o600)
    with host_lock(tmp_path/'lock') as lease, Journal(directory, lease) as journal:
        assert journal.db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert journal.rows() == []


def test_noncanonical_production_lease_and_uninstalled_code_block(setup, monkeypatch):
    from scripts.production_integration.locking import require_production_lease
    s = setup
    with s.lock() as lease:
        with pytest.raises(DockerError): require_production_lease(lease)
    from scripts.production_integration.installation import SealedInstallation
    c,envelope = approve(s)
    s.executor.installation = SealedInstallation(REVISION, ())
    assert s.executor.run(c,envelope).status == 'blocked'
    assert not s.world.calls


def test_candidate_content_is_deterministic_and_never_synthesized(setup):
    s = setup
    c = s.preflight.prepare('first-project-router-rollout').candidate
    assert c == s.preflight.prepare(c.scope).candidate
    from scripts.production_integration.artifacts import blob_digest
    assert c.manifest_hash == blob_digest(b'manifest') and c.policy_hash == blob_digest(b'policy')
    s.preflight.content_reader = lambda:(s.world.desired, b'', b'policy')
    with pytest.raises(DockerError, match='content-unverified'): s.preflight.prepare(c.scope)


def test_nonce_cannot_be_rebound_to_changed_resources(setup):
    s = setup
    c, envelope = approve(s)
    assert s.executor.run(c,envelope).status == 'succeeded'
    s.world.present, s.world.route = False, False
    s.preflight.contracts.collect = lambda:replace(s.contracts, storage=encode({'capacity':2**31}))
    new, envelope = approve(s)
    assert new.identity != c.identity
    assert s.executor.run(new,envelope).status == 'blocked'
    assert len(s.world.calls) == 2


def test_scoped_adapter_rejects_arbitrary_artifacts_services_and_endpoints(setup, monkeypatch):
    from scripts.production_integration.mutations import ScopedProductionDocker
    from scripts.production_integration.docker import EngineContract
    from scripts.compose import render_compose
    s = setup
    c, _ = approve(s)
    grant = SimpleNamespace(candidate=c, require=lambda:None)
    adapter = ScopedProductionDocker(EngineContract('unix:///run/docker.sock','daemon','a'*32,True),
        lambda:'a'*32, installation=ControlledInstallation(), grant=grant, application=s.world.app)
    monkeypatch.setattr(adapter, 'identity', lambda:('a'*32,'daemon'))
    artifact = render_compose(s.world.app)
    for wrong in (replace(artifact, project='other'), replace(artifact, model_json='{"command":"sh"}')):
        with pytest.raises(DockerError, match='scope-mismatch'): adapter.up(wrong, 'web')
    for service in ('--help','web;sh','$(id)', '../web'):
        with pytest.raises(DockerError, match='scope-mismatch'): adapter.up(artifact, service)
    adapter.contract = replace(adapter.contract, endpoint='ssh://foreign')
    # Test real identity gate before it could reach a process.
    monkeypatch.delattr(adapter, 'identity')
    with pytest.raises(DockerError, match='endpoint-not-approved'): adapter.up(artifact,'web')


def test_production_uses_shared_fixed_compose_commands(setup, monkeypatch):
    from scripts.production_integration import mutations
    from scripts.compose import render_compose
    s = setup
    calls = []
    monkeypatch.setenv('PYTHONPATH','/malicious')
    monkeypatch.setenv('DOCKER_HOST','ssh://malicious')
    monkeypatch.setenv('COMPOSE_FILE','/malicious')
    monkeypatch.setattr(mutations,'run_bounded',lambda argv,**kwargs:calls.append((argv,kwargs)))
    grant = SimpleNamespace(require=lambda:None)
    mutations.compose_up(render_compose(s.world.app), 'web', grant, ControlledInstallation())
    assert len(calls) == 2
    for argv, kwargs in calls:
        assert argv[:4] == ['/usr/bin/docker','--host','unix:///run/docker.sock','compose']
        assert '--env-file' in argv and '/dev/null' in argv and '--file' in argv
        assert 'malicious' not in encode(argv)
    assert calls[0][0][-2:] == ['config','-q']
    assert calls[1][0][-7:] == ['up','-d','--no-deps','--no-build','--pull','never','web']


def test_foreign_resources_preserved_during_engine_execution(setup):
    from test_deployment import resource
    s = setup
    s.world.foreign = tuple(resource(kind, 'foreign-'+kind, {}, mounts=[], networks={}) for kind in ('container','network','volume'))
    before = s.world.foreign
    c,envelope = approve(s)
    assert s.executor.run(c,envelope).status == 'succeeded'
    assert s.world.foreign == before
    assert s.world.calls == [('up','portfolio-project-router','web'),('gateway',)]


def test_unknown_bootstrap_mutation_never_replayed(setup):
    s = setup
    # Exercise BootstrapExecution with the same durable grant/journal directly.
    from scripts.production_integration.execution import ExecutionGrant
    from test_production_preparation import candidate
    c = replace(candidate(), scope='first-bootstrap', plan_hash=hashed({'different':'resources'}))
    claims = ApprovalClaims(c.identity,'operator',90,150,'3'*32)
    class Port:
        calls = []
        def preflight(self,*args): pass
        def apply(self, action, *args):
            self.calls.append(action)
            raise DockerError('docker-timeout',True)
    port = Port()
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        journal.consume_authorization(claims,c)
        grant = ExecutionGrant(c,lease,journal,claims)
        prepared = SimpleNamespace(bootstrap=SimpleNamespace(operations=('create-manager-proxy-with-reviewed-ipam',)))
        with pytest.raises(DockerError): BootstrapExecution(port).execute(prepared,grant)
        assert journal.incomplete()
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        with pytest.raises(JournalError): journal.consume_authorization(replace(claims,nonce='4'*32),c)
    assert len(port.calls) == 1


def test_migration_preserves_lkg_and_history(setup):
    s = setup
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        before = journal.rows()
        good = journal.last_good('manager-bootstrap','gateway')
        journal.db.execute('DROP TABLE authorizations')
        journal.db.execute('PRAGMA user_version=1')
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        assert journal.rows() == before
        assert journal.last_good('manager-bootstrap','gateway') == good
        # A pre-authorization v1 LKG cannot mint production bootstrap authority.
        assert not journal.production_bootstrap('a'*32,'daemon')


def test_gateway_transport_rejects_unapproved_text_and_never_reload_on_failure(setup, monkeypatch):
    from scripts.production_integration import gateway_write as module
    s = setup
    c,_ = approve(s)
    prepared = s.preflight.prepare(c.scope)
    calls = []
    class Read:
        last_snapshot = SimpleNamespace(container_id='a'*64)
        def read(self): return ()
    grant = SimpleNamespace(candidate=c, require=lambda:None, claims=SimpleNamespace(nonce='1'*32))
    installation = SimpleNamespace(verify=lambda:None, files=(('/opt/portfolio-deployment/tools/caddy','a'*64),))
    port = module.GrantedGatewayPort(installation,Read(),grant,prepared,
                                    SimpleNamespace(identity=lambda:(c.host,c.daemon)))
    monkeypatch.setattr(module,'protected',lambda *args:None)
    monkeypatch.setattr(module,'run_bounded',lambda argv,**kwargs:calls.append(argv))
    with pytest.raises(DockerError): port.validate('arbitrary { shell }')
    with pytest.raises(DockerError): port.compare_and_reload(port.old,port.new)
    assert not calls
    def failed(*args, **kwargs): raise DockerError('caddy-validation-failed')
    monkeypatch.setattr(module,'run_bounded',failed)
    with pytest.raises(DockerError): port.validate(port.new)
    assert port.validated is False
    with pytest.raises(DockerError): port.preserve_lkg(port.old)
    assert not calls


def test_installation_context_and_metadata_reject_writable_or_replaced_code(monkeypatch):
    import stat
    from pathlib import Path
    from scripts.production_integration import installation
    with pytest.raises(DockerError): installation.SealedInstallation(REVISION,()).verify()
    change = {}
    def metadata(path):
        is_file = str(path) == '/opt/portfolio-deployment/launcher.py'
        values = dict(st_uid=0,st_mode=(stat.S_IFREG|0o644) if is_file else (stat.S_IFDIR|0o755),st_nlink=1)
        if str(path) == change.get('path'): values.update(change['values'])
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path,'lstat',metadata)
    installation.protected('/opt/portfolio-deployment/launcher.py')
    for path, values in [('/opt',{'st_mode':stat.S_IFDIR|0o777}),
                         ('/opt/portfolio-deployment',{'st_uid':1000}),
                         ('/opt/portfolio-deployment/launcher.py',{'st_mode':stat.S_IFLNK|0o777}),
                         ('/opt/portfolio-deployment/launcher.py',{'st_nlink':2})]:
        change.update(path=path,values=values)
        with pytest.raises(DockerError): installation.protected('/opt/portfolio-deployment/launcher.py')


def test_ledger_storage_failure_prevents_intent_and_mutation(setup, monkeypatch):
    s = setup
    c,envelope = approve(s)
    def unavailable(*args): raise JournalError('journal-write-failed')
    monkeypatch.setattr(Journal,'consume_authorization',unavailable)
    assert s.executor.run(c,envelope).status == 'blocked'
    assert not s.world.calls


def test_actual_process_death_retains_consumption_and_releases_flock(setup):
    """OS closes the lock/DB; committed consumption survives without finally."""
    s = setup
    c,envelope = approve(s)
    child = os.fork()
    if child == 0:
        from scripts.production_integration import execution
        execution.ExecutionGrant.__init__ = lambda *args:os._exit(23)
        try:
            s.executor.run(c,envelope)
        finally:
            os._exit(24)
    _,status = os.waitpid(child,0)
    assert os.waitstatus_to_exitcode(status) == 23
    assert s.executor.run(c,envelope).status == 'blocked'
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        assert not journal.incomplete()
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?",('1'*32,)).fetchone()[0] == 'consumed'
    assert not s.world.calls


@pytest.mark.parametrize('reload_failure', [False, True])
def test_gateway_positive_commands_are_fixed_and_lkg_precedes_reload(setup, monkeypatch, reload_failure):
    from pathlib import Path
    from scripts.production_integration import gateway_write as module
    s = setup
    c,_ = approve(s)
    prepared = s.preflight.prepare(c.scope)
    events = []
    class Read:
        last_snapshot = SimpleNamespace(container_id='d'*64)
        def read(self): return ()
        def verify_current(self): events.append(('runtime',))
    grant = SimpleNamespace(candidate=c,require=lambda:None,claims=SimpleNamespace(nonce='1'*32))
    installation = SimpleNamespace(verify=lambda:None,files=(('/opt/portfolio-deployment/tools/caddy','a'*64),))
    port = module.GrantedGatewayPort(installation,Read(),grant,prepared,
                                    SimpleNamespace(identity=lambda:(c.host,c.daemon)))
    monkeypatch.setattr(module,'protected',lambda *args:None)
    monkeypatch.setattr(module,'validate_storage_directory',lambda *args,**kwargs:None)
    def run(argv, **kwargs):
        events.append(('command', argv))
        if argv[1] == 'reload':
            assert Path(argv[5]).read_bytes() == port.new.encode()
            if reload_failure: raise DockerError('command-failed')
    monkeypatch.setattr(module,'run_bounded',run)
    monkeypatch.setattr(module,'durable_replace',lambda directory,name,raw:events.append(('durable',directory,name)))
    original_read = Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda path:port.old.encode() if str(path)=='/srv/portfolio/caddy-config/Caddyfile' else original_read(path))
    port.validate(port.new)
    port.preserve_lkg(port.old)
    if reload_failure:
        with pytest.raises(DockerError, match='outcome-unknown') as error:
            port.compare_and_reload(port.old,port.new)
        assert error.value.unknown
    else:
        port.compare_and_reload(port.old,port.new)
    assert events[0][1][1] == 'adapt' and events[1][1][1] == 'validate'
    assert events[2] == ('durable','/srv/portfolio/caddy-lkg','1'*32+'.Caddyfile')
    assert events[3] == ('runtime',)
    assert events[4] == ('durable','/srv/portfolio/caddy-config','Caddyfile')
    assert events[5] == ('runtime',)
    argv = events[6][1]
    assert argv[:5] == ['/opt/portfolio-deployment/tools/caddy', 'reload', '--address',
                        'unix//srv/portfolio/caddy-admin/admin.sock|0600', '--config']
    assert argv[5].endswith('/Caddyfile') and argv[6:] == ['--adapter','caddyfile']
    if not reload_failure: assert events[7] == ('runtime',)


def test_receipt_write_failure_after_mutation_never_reports_blocked(setup,monkeypatch):
    s = setup
    c,envelope = approve(s)
    def failed(*args): raise JournalError('journal-write-failed')
    monkeypatch.setattr(Journal,'finish_authorization',failed)
    assert s.executor.run(c,envelope).status == 'recovery-required'
    assert s.world.calls
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        assert journal.db.execute("SELECT state FROM authorizations WHERE nonce=?",('1'*32,)).fetchone()[0] == 'consumed'


def test_invalid_legacy_journal_rolls_back_migration(setup):
    s = setup
    with s.lock() as lease, Journal(s.directory,lease) as journal:
        journal.db.execute('DROP TABLE authorizations')
        journal.db.execute('PRAGMA user_version=1')
        journal.db.execute("UPDATE attempts SET runtime='invalid'")
    with s.lock() as lease:
        with pytest.raises(JournalError):
            with Journal(s.directory,lease): pass
    with sqlite3.connect(s.directory/'journal.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='authorizations'").fetchone() is None


def test_granted_uds_reload_failure_never_promotes_lkg_and_fences_replay(setup, monkeypatch):
    from pathlib import Path
    from scripts.production_integration import gateway_write as module
    s = setup
    c, envelope = approve(s)
    events = []
    class Read:
        last_snapshot = SimpleNamespace(container_id='d'*64)
        def read(self): return ()
        def verify_current(self): pass
    installation = SimpleNamespace(verify=lambda:None, files=((module.BINARY, 'a'*64),))
    from functools import partial
    monkeypatch.setattr(module, 'ProductionGateway', partial(module.ProductionGateway,
        lease_verifier=lambda lease: owns_host_lock(lease)))
    s.executor.gateway = module.GatewayExecutionFactory(installation, Read(), s.preflight.engine)
    monkeypatch.setattr(module, 'protected', lambda *a:None)
    monkeypatch.setattr(module, 'validate_storage_directory', lambda *a, **kw:None)
    monkeypatch.setattr(module, 'durable_replace', lambda directory, name, raw: events.append(('preserve', directory, raw)))
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: module.content(()).encode()
        if str(p) == '/srv/portfolio/caddy-config/Caddyfile' else original(p))
    def run(argv, **kwargs):
        events.append(('command', argv[1]))
        if argv[1] == 'reload': raise DockerError('caddy-load-rejected')
    monkeypatch.setattr(module, 'run_bounded', run)
    result = s.executor.run(c, envelope)
    assert result.status == 'recovery-required'
    assert ('command', 'reload') in events
    assert next(e for e in events if e[0] == 'preserve')[1] == '/srv/portfolio/caddy-lkg'
    with s.lock() as lease, Journal(s.directory, lease) as journal:
        assert journal.last_good('project-router', 'web') is None
        assert journal.authorization_recovery()
    count = len(events)
    assert s.executor.run(c, envelope).status == 'blocked'
    assert len(events) == count
