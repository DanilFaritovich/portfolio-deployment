"""Offline abandonment keeps failed journals and resources, never replays."""
from copy import deepcopy
import json

import pytest

from scripts.docker_adapter import DockerError
from scripts.e2e import control, failed_run_recovery as recovery
from scripts.e2e.host_state import Store
from scripts.e2e.runner import SCENARIOS
from test_e2e_prepared_recovery import setup, images, SESSION


class Machine:
    state = 'Running'
    operations = []
    changed_report = False
    processes = b'/sbin/init\n'
    error = False

    def __init__(self, session):
        self.session, self.name = session, 'pd-e2e-' + session

    def info(self):
        return {'state': self.state, 'image_hash': 'a' * 64, 'image_release': '24.04', 'mounts': {}}

    def command(self, *args, **kwargs):
        if args[0] == 'list':
            return json.dumps({'list': [{'name': self.name, 'state': self.state}]}).encode()
        assert args == ('stop', self.name)
        self.operations.append('stop')
        if self.error:
            raise DockerError('stop-outcome-unknown')
        type(self).state = 'Stopped'

    def invoke(self, action, *args):
        assert action == 'diagnose'
        return {'status': 'passed', 'report': {} if self.changed_report else self.report}

    def guest(self, *args):
        if args[0] == '/usr/bin/ps':
            return self.processes
        assert 'mode=ro' in args[-1] and 'host_lock' in args[-1] and 'recover(' not in args[-1]
        return json.dumps([{'journal': 'journal', 'attempts': [{'state': 'recovery-required'}],
                            'sha256': 'b' * 64}]).encode()


@pytest.fixture
def failed(setup):
    path, state = setup
    report = {'version': 1, 'mode': 'real', 'status': 'failed', 'realE2E': 'performed', 'scenarios': [
        {'scenario': s, 'status': 'failed' if i == 0 else 'not-performed', 'evidence': {}}
        for i, s in enumerate(SCENARIOS)]}
    state.update(phase='run', status='failed', result=report)
    with Store(path).locked() as store:
        store.write(SESSION + '.json', state)
    Machine.state, Machine.operations, Machine.changed_report = 'Running', [], False
    Machine.processes, Machine.error, Machine.report = b'/sbin/init\n', False, deepcopy(report)
    return path, state


def test_abandon_known_failure_stops_only_owned_vm_preserves_receipt(failed):
    path, _ = failed
    original = (path / (SESSION + '.json')).read_bytes()
    prompts = []
    result = recovery.close(path, SESSION, factory=Machine, consent=lambda *a: prompts.append(a[1]))
    assert result['journal'] == 'preserved-unresolved-no-replay'
    assert Machine.operations == ['stop'] and Machine.state == 'Stopped'
    assert prompts == ['stop-failed-run:' + SESSION, 'close-failed-run:' + SESSION]
    assert (path / (SESSION + '.json')).read_bytes() == original
    control.preflight()
    with pytest.raises(DockerError):
        with control.guard(path, 'state'):
            pytest.fail('replayed failed session')
    (control.root() / ('failed-' + SESSION) / 'review.json').unlink()
    with pytest.raises(DockerError, match='retained-evidence-changed'):
        control.preflight()


@pytest.mark.parametrize('change', ['unknown', 'report', 'process', 'pending', 'missing-vm', 'stop-error', 'decline'])
def test_uncertainty_or_decline_preserves_fence(failed, change):
    path, state = failed
    if change == 'unknown':
        state['status'] = 'unknown'
        with Store(path).locked() as store:
            store.write(SESSION + '.json', state)
    elif change == 'report':
        Machine.changed_report = True
    elif change == 'process':
        Machine.processes = b'python /opt/portfolio-e2e/scripts/e2e_guest.py run\n'
    elif change == 'pending':
        (path / 'left.pending').touch(mode=0o600)
    elif change == 'missing-vm':
        Machine.state = 'Deleted'
    elif change == 'stop-error':
        Machine.error = True
    def consent(*args):
        if change == 'decline':
            raise DockerError('e2e-operation-declined')
    with pytest.raises(DockerError):
        recovery.close(path, SESSION, factory=Machine, consent=consent)
    assert Machine.operations == (['stop'] if change == 'stop-error' else [])
    with pytest.raises(DockerError):
        control.preflight()
