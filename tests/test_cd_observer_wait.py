"""Offline regressions for observer/preflight contention and sealed diagnostics."""
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from installation import cd_entry
from scripts.cd import application, bridge, runner
from scripts.docker_adapter import DockerError


def test_wait_defers_sealed_verification_until_new_public_challenge(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'STATE', tmp_path)
    monkeypatch.setattr(bridge, 'protected', lambda path: None)
    now, checks, waits = [1000.0], [], []
    monkeypatch.setattr(bridge.time, 'time', lambda: now[0])
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: now[0])
    path = tmp_path / 'challenge.json'
    # Even a recent challenge from an earlier operation must not wake this run.
    path.write_bytes(bridge.canonical({'created': 999, 'policy_id': 'reviewed'}))
    def sleep(seconds):
        assert checks == []  # preflight is still collecting its fresh host facts
        waits.append(seconds)
        now[0] += seconds
        if len(waits) == 4:
            path.write_bytes(bridge.canonical({'created': 1001, 'policy_id': 'reviewed'}))
    monkeypatch.setattr(bridge.time, 'sleep', sleep)
    def installation():
        checks.append('installation')
        return 'verified-installation'
    def policy(installed):
        assert installed == 'verified-installation'
        checks.append('policy')
        return SimpleNamespace(identity='reviewed')
    monkeypatch.setattr(bridge, 'installation', installation)
    monkeypatch.setattr(bridge, 'installed_policy', policy)
    response = bridge.handle('observer', {'op': 'observe-wait', 'not_before': 1000}, None)
    assert response == {'created': 1001, 'policy_id': 'reviewed'}
    assert checks == ['installation', 'policy'] and len(waits) == 4


def test_wait_is_bounded_without_challenge_and_does_not_verify_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'STATE', tmp_path)
    now = [0.0]
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(bridge, 'OBSERVER_WAIT_SECONDS', 1)
    monkeypatch.setattr(bridge.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(bridge, 'installation', lambda: pytest.fail('no public challenge yet'))
    with pytest.raises(DockerError, match='cd-observer-wait-timeout'):
        bridge.handle('observer', {'op': 'observe-wait', 'not_before': 1000}, None)
    assert now[0] == 1


@pytest.mark.parametrize('threshold', [True, 0, -1, '1000', None])
def test_wait_rejects_invalid_threshold_before_verification(monkeypatch, threshold):
    monkeypatch.setattr(bridge, 'installation', lambda: pytest.fail('invalid request'))
    with pytest.raises(DockerError, match='cd-observer-wait-invalid'):
        bridge.handle('observer', {'op': 'observe-wait', 'not_before': threshold}, None)


def test_wait_does_not_skip_policy_verification_when_challenge_arrives(monkeypatch):
    checks = []
    monkeypatch.setattr(bridge, 'wait_for_challenge', lambda threshold: checks.append('wait'))
    monkeypatch.setattr(bridge, 'installation', lambda: checks.append('installation'))
    def fail(installed):
        raise DockerError('cd-policy-expired')
    monkeypatch.setattr(bridge, 'installed_policy', fail)
    monkeypatch.setattr(bridge, 'observe', lambda policy: pytest.fail('unverified policy'))
    with pytest.raises(DockerError, match='cd-policy-expired'):
        bridge.handle('observer', {'op': 'observe-wait', 'not_before': 1000}, None)
    assert checks == ['wait', 'installation']


def test_wait_cannot_carry_witness_or_automation_payload(monkeypatch):
    monkeypatch.setattr(bridge, 'installation', lambda: pytest.fail('invalid scope'))
    with pytest.raises(DockerError, match='cd-observer-scope-forbidden'):
        bridge.handle('observer', {'op': 'observe-wait', 'not_before': 1000, 'receipt': {}}, None)


@pytest.mark.parametrize('stuck', [False, True])
def test_cancelling_wait_terminates_and_reaps_ssh(monkeypatch, stuck):
    stop = threading.Event()
    events = []
    class Process:
        returncode = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self): return self.returncode
        def communicate(self, input=None, timeout=None):
            events.append(('communicate', input, timeout))
            if timeout == .25:
                stop.set()
                raise subprocess.TimeoutExpired('ssh', timeout)
            if timeout == 5 and stuck:
                raise subprocess.TimeoutExpired('ssh', timeout)
            self.returncode = -9 if stuck else -15
            return b'', b''
        def terminate(self): events.append('terminate')
        def kill(self): events.append('kill')
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: Process())
    with pytest.raises(DockerError, match='cd-observer-cancelled'):
        runner.Client.cancellable_call(['ssh'], b'request', 1230, stop)
    assert 'terminate' in events and ('kill' in events) is stuck
    assert sum(isinstance(e, tuple) and e[1] == b'request' for e in events) == 1


def test_cancellable_wait_sends_request_only_once_across_timeouts(monkeypatch):
    sent = []
    class Process:
        returncode = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self): return self.returncode
        def communicate(self, input=None, timeout=None):
            sent.append(input)
            if len(sent) == 1:
                raise subprocess.TimeoutExpired('ssh', timeout)
            self.returncode = 0
            return b'{"created":1000}', b''
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: Process())
    result = runner.Client.cancellable_call(['ssh'], b'request', 1230, threading.Event())
    assert sent == [b'request', None] and result.returncode == 0


def test_application_observer_uses_cancellable_wait_and_retains_freshness_check(monkeypatch):
    stop = threading.Event()
    calls = []
    def call(request, **kwargs):
        calls.append((request, kwargs))
        # A long RPC timeout must never make an old public proof acceptable.
        return {'created': 900, 'policy_id': 'reviewed'}
    monkeypatch.setattr(runner.time, 'time', lambda: 1000)
    monkeypatch.setenv('CD_POLICY_ID', 'reviewed')
    observer = object.__new__(runner.Client)
    monkeypatch.setattr(observer, 'call', call)
    # Run one iteration; no real sleep, SSH, HTTPS or signature.
    waits = [False, True]
    monkeypatch.setattr(stop, 'wait', lambda seconds: waits.pop(0))
    monkeypatch.setattr(runner, 'PublicPolicy', lambda **kw: pytest.fail('stale challenge'))
    observer.observer(stop, wait=True)
    request, kwargs = calls[0]
    assert request == {'op': 'observe-wait', 'not_before': 1000}
    assert kwargs == {'role': 'observer', 'timeout': 1230, 'stop': stop}
    assert len(calls) == 1


@pytest.mark.parametrize('error,diagnostic', [
    (DockerError('production-host-facts-changed-or-expired'), 'production-host-facts-changed-or-expired'),
    (RuntimeError('PRIVATE-SENTINEL'), None),
])
def test_installed_application_preserves_only_reviewed_diagnostic(monkeypatch, capsys, error, diagnostic):
    def fail(installed): raise error
    monkeypatch.setattr(application, 'main', fail)
    assert cd_entry.run_application(object()) == 2
    output = capsys.readouterr().out
    response = bridge.closed_json(output.strip().encode(), bridge.HEADER_LIMIT)
    assert response['status'] == 'blocked' and response['reason'] == 'cd-installed-entry-failed'
    assert response.get('diagnostic') == diagnostic and 'PRIVATE-SENTINEL' not in output


def test_cancellable_wait_deadline_terminates_ssh_without_retry(monkeypatch):
    now, launches = [0.0], []
    monkeypatch.setattr(runner.time, 'monotonic', lambda: now[0])
    class Process:
        returncode = None
        terminated = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def poll(self): return self.returncode
        def communicate(self, input=None, timeout=None):
            if self.terminated:
                self.returncode = -15
                return b'', b''
            now[0] = 1231
            raise subprocess.TimeoutExpired('ssh', timeout)
        def terminate(self): self.terminated = True
    def launch(*args, **kwargs):
        launches.append(args)
        return Process()
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.Client.cancellable_call(['ssh'], b'request', 1230, threading.Event())
    assert len(launches) == 1
