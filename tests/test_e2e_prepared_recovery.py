"""Offline regression boundaries for a lost post-pull confirmation terminal."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import base64

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import control, orchestration, prepared_recovery as recovery
from scripts.e2e.host_state import Store
from scripts.e2e.runner import SCENARIOS
from test_local_e2e import images  # Reuse synthetic immutable manifest fixture.

SESSION = '796bb4381c03dc62'


class Machine:
    def __init__(self, session):
        self.session, self.name = session, 'pd-e2e-' + session

    def invoke(self, action, *args):
        if action == 'diagnose':
            return {'status': 'passed', 'report': {'realE2E': 'not-performed'}}
        assert action == 'run'
        self.calls.append(action)
        if self.error:
            raise DockerError('transport-uncertain')
        return deepcopy(self.result)

    def guest(self, *args):
        return json.dumps(self.names).encode()


@pytest.fixture
def setup(tmp_path, monkeypatch, images):
    path = tmp_path / 'host-state' / 'e2e-state'
    path.parent.mkdir(mode=0o700, parents=True)
    path.mkdir(mode=0o700)
    snapshot = path / SESSION
    snapshot.mkdir(mode=0o700)
    lock_path = tmp_path / 'lock.json'
    for name in ['lock.json', *[r['manifest'] for r in json.loads(lock_path.read_text())['images'].values()]]:
        (snapshot / name).write_bytes((tmp_path / name).read_bytes())
        (snapshot / name).chmod(0o600)
    state = {'version': 1, 'session': SESSION, 'vm': 'pd-e2e-' + SESSION, 'phase': 'images', 'status': 'planned',
             'mutationStarted': True, 'images': orchestration.image_fingerprint(snapshot / 'lock.json'),
             'identity': {'session': SESSION, 'machine': 'machine', 'daemon': 'daemon'},
             'result': {'status': 'passed', 'images': 6, 'realE2E': 'not-performed'}}
    with Store(path).locked() as store:
        store.write('current.json', {'version': 1, 'session': SESSION})
        store.write(SESSION + '.json', state)
    with Store(control.root()).locked() as fence:
        fence.write('active.json', {'kind': 'state', 'directory': str(path)})
    monkeypatch.setattr(orchestration, 'identity', lambda machine: state['identity'])
    Machine.names, Machine.calls, Machine.error = ['identity.json'], [], False
    Machine.result = {'status': 'passed', 'mode': 'real', 'realE2E': 'performed',
                      'scenarios': [{'scenario': s, 'status': 'passed', 'evidence': {}} for s in SCENARIOS]}
    return path, state


def archive_stop(path, state):
    after = {**state, 'status': 'blocked', 'result': {'reason': 'e2e-interrupted-or-unconfirmed',
                                                    'realE2E': 'not-performed'}}
    with Store(path).locked() as store:
        store.write(SESSION + '.json', after)
    with Store(control.root() / ('prepared-' + SESSION)).locked() as archive:
        archive.write('stop.json', {'version': 1, 'directory': str(path), 'session': SESSION, 'before': state})
        archive.write('stopped.json', {'receipt': after, 'receiptSha256': control.digest(after)})
    return after


def test_stop_pins_pid_and_archives_before_sigint(setup, monkeypatch):
    path, state = setup
    handle = os.open('/dev/null', os.O_RDONLY)
    monkeypatch.setattr(os, 'pidfd_open', lambda pid: handle)
    monkeypatch.setattr(recovery, 'process', lambda pid, path: {'pid': pid, 'tty': '/dev/pts/2'})
    monkeypatch.setattr(recovery.select, 'select', lambda *a: ([handle], [], []))
    def interrupt(fd, sig):
        assert fd == handle and sig == signal.SIGINT
        with recovery.read_store(control.root() / ('prepared-' + SESSION)) as archive:
            assert archive.read('stop.json')['before'] == state
        after = {**state, 'status': 'blocked', 'result': {'reason': 'e2e-interrupted-or-unconfirmed',
                                                        'realE2E': 'not-performed'}}
        with Store(path).locked() as store:
            store.write(SESSION + '.json', after)
    monkeypatch.setattr(signal, 'pidfd_send_signal', interrupt)
    confirmed = []
    result = recovery.stop(path, SESSION, 123, consent=lambda *a: confirmed.append(a), factory=Machine)
    assert result['status'] == 'passed' and len(confirmed) == 1 and not Machine.calls
    with pytest.raises(DockerError, match='stop-already-attempted'):
        recovery.stop(path, SESSION, 123, factory=Machine)


def test_continuation_preserves_original_and_runs_only_once(setup):
    path, state = setup
    archive_stop(path, state)
    raw = (path / (SESSION + '.json')).read_bytes()
    inodes = [(p.stat().st_ino) for p in (path / 'controller.lock', control.root() / 'controller.lock')]
    confirmed = []
    result = recovery.continue_prepared(path, SESSION, factory=Machine, consent=lambda *a: confirmed.append(a[1]))
    assert result['status'] == 'passed' and Machine.calls == ['run']
    assert confirmed == ['run:' + SESSION, 'activate:' + SESSION]
    assert (path / (SESSION + '.json')).read_bytes() == raw
    assert inodes == [p.stat().st_ino for p in (path / 'controller.lock', control.root() / 'controller.lock')]
    control.preflight()
    with pytest.raises(DockerError, match='run-already-attempted'):
        recovery.continue_prepared(path, SESSION, factory=Machine)


@pytest.mark.parametrize('kind', ['identity', 'registry', 'report', 'receipt', 'lock', 'pending'])
def test_uncertainty_blocks_without_run(setup, monkeypatch, kind):
    path, state = setup
    archive_stop(path, state)
    if kind == 'identity':
        monkeypatch.setattr(orchestration, 'identity', lambda machine: {'changed': True})
    elif kind in ('registry', 'report'):
        Machine.names.append(kind)
    elif kind == 'receipt':
        with Store(path).locked() as store:
            value = store.read(SESSION + '.json')
            value['status'] = 'unknown'
            store.write(SESSION + '.json', value)
    elif kind == 'lock':
        (path / SESSION / 'lock.json').write_text('{}')
    else:
        (path / 'uncertain.pending').touch(mode=0o600)
    with pytest.raises((DockerError, KeyError, ValueError)):
        recovery.continue_prepared(path, SESSION, factory=Machine, consent=lambda *a: None)
    assert not Machine.calls


def test_declined_continuation_preserves_recoverability(setup):
    path, state = setup
    archive_stop(path, state)
    def decline(*a):
        raise DockerError('e2e-operation-declined')
    with pytest.raises(DockerError, match='declined'):
        recovery.continue_prepared(path, SESSION, factory=Machine, consent=decline)
    with Store(control.root() / ('prepared-' + SESSION)).locked() as archive:
        assert archive.read('run.json') is None
    assert not Machine.calls


@pytest.mark.parametrize('outcome', ['failed', 'exception', 'incomplete'])
def test_failed_or_unknown_run_blocks_replay_and_new_work(setup, outcome):
    path, state = setup
    archive_stop(path, state)
    if outcome == 'exception':
        Machine.error = True
    elif outcome == 'incomplete':
        Machine.result['scenarios'] = []
    else:
        Machine.result['status'] = 'failed'
    try:
        result = recovery.continue_prepared(path, SESSION, factory=Machine, consent=lambda *a: None)
        assert result['status'] == 'failed'
    except DockerError:
        assert outcome in ('exception', 'incomplete')
    with pytest.raises(DockerError):
        recovery.continue_prepared(path, SESSION, factory=Machine, consent=lambda *a: None)
    with pytest.raises(DockerError):
        control.preflight()
    assert Machine.calls == ['run']


def test_retained_recovery_evidence_cannot_disappear(setup):
    path, state = setup
    archive_stop(path, state)
    recovery.continue_prepared(path, SESSION, factory=Machine, consent=lambda *a: None)
    (control.root() / ('prepared-' + SESSION) / 'run.json').unlink()
    with pytest.raises(DockerError, match='retained-evidence-missing'):
        control.preflight()


@pytest.mark.parametrize('phase,status', [('run', 'unknown'), ('prepare', 'planned'), ('images', 'in-progress')])
def test_only_completed_pulls_are_eligible(setup, phase, status):
    _, state = setup
    state.update(phase=phase, status=status)
    with pytest.raises(DockerError, match='completed-images-unstarted-run'):
        recovery.prepared(state, SESSION)


@pytest.mark.parametrize('change', ['none', 'command', 'child', 'wchan', 'lock', 'tty'])
def test_process_identity_and_idle_lock_owner_required(setup, monkeypatch, tmp_path, change):
    path, _ = setup
    proc_root = tmp_path / 'proc'
    proc = proc_root / '123'
    (proc / 'task/123').mkdir(parents=True)
    (proc / 'task/123/children').write_text('55' if change == 'child' else '')
    (proc / 'fd').mkdir()
    (proc / 'fdinfo').mkdir()
    (proc / 'cwd').symlink_to(recovery.ROOT, target_is_directory=True)
    command = [str(recovery.ROOT / '.venv/bin/python'), '-m', 'scripts.e2e.cli',
               'fixtures' if change == 'command' else 'run']
    (proc / 'cmdline').write_bytes(b'\0'.join(s.encode() for s in command) + b'\0')
    (proc / 'stat').write_text('123 (python) S ' + ' '.join(['1'] * 30))
    (proc / 'wchan').write_text('other' if change == 'wchan' else 'wait_woken')
    for number in ('0', '1'):
        (proc / 'fd' / number).symlink_to('/dev/pts/2' if change != 'tty' else '/dev/null')
        (proc / 'fdinfo' / number).write_text('')
    for number, lock in enumerate((control.root() / 'controller.lock', path / 'controller.lock'), 5):
        (proc / 'fd' / str(number)).symlink_to(lock)
        (proc / 'fdinfo' / str(number)).write_text('' if change == 'lock' else
            'lock:\t1: FLOCK  ADVISORY  WRITE 123 103:02:1234 0 EOF\n')
    monkeypatch.setattr(recovery, 'PROC', proc_root)
    if change == 'none':
        assert recovery.process(123, path)['pid'] == 123
    else:
        with pytest.raises(DockerError):
            recovery.process(123, path)


def test_stop_declined_never_sends_signal(setup, monkeypatch):
    path, _ = setup
    monkeypatch.setattr(os, 'pidfd_open', lambda pid: os.open('/dev/null', os.O_RDONLY))
    monkeypatch.setattr(recovery, 'process', lambda *a: {'pid': 123, 'tty': '/dev/pts/2'})
    def forbidden(*a):
        pytest.fail('signal sent after declined approval')
    monkeypatch.setattr(signal, 'pidfd_send_signal', forbidden)
    def decline(*a):
        raise DockerError('e2e-operation-declined')
    with pytest.raises(DockerError, match='declined'):
        recovery.stop(path, SESSION, 123, consent=decline, factory=Machine)
    with Store(control.root() / ('prepared-' + SESSION)).locked() as archive:
        assert archive.read('stop.json') is None


def blocked_bootstrap():
    return {'version': 1, 'mode': 'real', 'status': 'blocked', 'realE2E': 'performed', 'scenarios': [
        {'scenario': s, 'status': 'blocked' if i == 0 else 'not-performed',
         'evidence': {'reason': 'docker-command-failed'} if i == 0 else {}}
        for i, s in enumerate(SCENARIOS)]}


class BootstrapMachine(Machine):
    def invoke(self, action, *args):
        assert action == 'diagnose'
        return {'status': 'passed', 'report': blocked_bootstrap()}

    def guest(self, *args):
        if args[0] == '/usr/bin/docker':
            return (self.name + '-unrelated\n').encode()
        if args[0] == '/usr/bin/ps':
            return b'/sbin/init\n/usr/bin/caddy run --config /etc/portfolio-e2e-caddy.json\n'
        if '-I' in args:
            from scripts.e2e.gateway import configuration
            return json.dumps([200, base64.b64encode(json.dumps(configuration()).encode()).decode()]).encode()
        return json.dumps(['baseline.json', 'identity.json', 'registry', 'report.json']).encode()


def test_close_failed_bootstrap_keeps_all_receipts_and_no_replay(setup):
    path, state = setup
    after = archive_stop(path, state)
    attempt = {'version': 1, 'status': 'blocked', 'originalReceiptSha256': control.digest(after),
               'result': blocked_bootstrap()}
    archive_path = control.root() / ('prepared-' + SESSION)
    with Store(archive_path).locked() as archive:
        archive.write('run.json', attempt)
    original = (path / (SESSION + '.json')).read_bytes()
    original_run = (archive_path / 'run.json').read_bytes()
    result = recovery.abandon_bootstrap(path, SESSION, factory=BootstrapMachine, consent=lambda *a: None)
    assert result['decision'] == 'closed-failed-bootstrap-no-replay'
    assert (path / (SESSION + '.json')).read_bytes() == original
    assert (archive_path / 'run.json').read_bytes() == original_run
    control.preflight()
    with pytest.raises(DockerError):
        recovery.continue_prepared(path, SESSION, factory=Machine)
    (archive_path / 'abandoned-bootstrap.json').unlink()
    with pytest.raises(DockerError, match='bootstrap-review-changed'):
        control.preflight()


@pytest.mark.parametrize('change', ['run-process', 'journal', 'container', 'report', 'caddy'])
def test_bootstrap_closure_rejects_unknown_or_deployment_evidence(setup, change):
    _, state = setup
    class Changed(BootstrapMachine):
        def invoke(self, *args):
            doc = super().invoke(*args)
            if change == 'report':
                doc['report']['status'] = 'failed'
            return doc

        def guest(self, *args):
            if change == 'run-process' and args[0] == '/usr/bin/ps':
                return b'python /opt/portfolio-e2e/scripts/e2e_guest.py run\n'
            if change == 'journal' and '-c' in args:
                return json.dumps(['baseline.json', 'identity.json', 'journal', 'registry', 'report.json']).encode()
            if change == 'container' and args[0] == '/usr/bin/docker':
                return b'deployment-container\n'
            if change == 'caddy' and '-I' in args:
                return json.dumps([200, base64.b64encode(b'{}').decode()]).encode()
            return super().guest(*args)
    with pytest.raises(DockerError):
        recovery.bootstrap_observation(Changed(SESSION), state, {'result': blocked_bootstrap()})
