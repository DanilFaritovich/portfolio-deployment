"""Abandon a known failed real run in a stopped VM; never reset its journal."""
import json
import os
from pathlib import Path

from scripts.e2e import control, orchestration
from scripts.e2e.host_state import Store
from scripts.e2e.safety import require, session_id
from scripts.e2e.vm import VM


def terminal(state, session):
    from scripts.e2e.runner import SCENARIOS
    report = state.get('result', {})
    require(state.get('session') == session and state.get('phase') == 'run' and
            state.get('status') in ('failed', 'blocked') and report.get('status') == state['status'] and
            report.get('mode') == 'real' and report.get('realE2E') == 'performed' and
            [r['scenario'] for r in report.get('scenarios', [])] == list(SCENARIOS) and
            all(r['status'] in ('passed', 'failed', 'blocked', 'not-performed') for r in report['scenarios']),
            'failed-run-known-terminal-report-required')


def snapshot(machine, required):
    listing = control.inventory(machine)
    matches = [v for v in listing['list'] if v['name'] == machine.name]
    info = machine.info()
    require(len(matches) == 1 and matches[0].get('state') == required and info.get('state') == required and
            not info.get('mounts') and isinstance(info.get('image_hash'), str) and len(info['image_hash']) == 64,
            'failed-run-vm-state-or-identity-unverified')
    return {k: info.get(k) for k in ('image_hash', 'image_release')}


def observation(machine, state):
    require(orchestration.identity(machine) == state['identity'], 'failed-run-guest-identity-changed')
    report = machine.invoke('diagnose', f'diagnose:{machine.session}')
    require(report.get('status') == 'passed' and report.get('report') == state['result'],
            'failed-run-guest-report-changed')
    processes = machine.guest('/usr/bin/ps', '-eo', 'args=').decode().splitlines()
    require(not any(('e2e_guest.py' in p and 'run' in p.split()) or
                    ('docker' in p.split() and 'compose' in p.split()) for p in processes),
            'failed-run-guest-operation-still-running')
    # Read-only SQLite queries retain and explain incomplete attempts; never use
    # Journal's mutating APIs and never call recovery replay. Collect under flock.
    code = '''import sys,json,sqlite3,pathlib,hashlib
sys.path.insert(0, '/opt/portfolio-e2e')
from scripts.deployment import host_lock
root = pathlib.Path(SESSION_PATH)
out = []
with host_lock():
    paths = sorted(root.glob('*/journal.sqlite3'))
    assert len(paths) <= 32
    for p in paths:
        assert not p.is_symlink() and p.stat().st_size <= 67108864
        c = sqlite3.connect('file:' + str(p) + '?mode=ro', uri=True)
        c.row_factory = sqlite3.Row
        assert c.execute('pragma quick_check').fetchall()[0][0] == 'ok'
        rows = [dict(r) for r in c.execute('select component,state,reason,readiness,probes,consecutive from attempts order by run,component')]
        assert len(rows) <= 256
        out.append({'journal': p.parent.name, 'attempts': rows, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        c.close()
print(json.dumps(out))
'''.replace('SESSION_PATH', repr('/var/lib/portfolio-e2e/' + machine.session))
    journals = json.loads(machine.guest('/opt/portfolio-e2e/.venv/bin/python', '-c', code))
    require(isinstance(journals, list) and len(journals) <= 32, 'failed-run-journal-observation-invalid')
    return {'identity': state['identity'], 'report': report['report'], 'guestOperations': 'absent',
            'journals': journals, 'recovery': 'analysis-only; retained without replay or reset'}


def close(path, session, *, consent=orchestration.confirm, factory=VM):
    session_id(session)
    with Store(control.root()).locked() as fence, Store(path).locked() as store, \
            Store(control.root() / ('failed-' + session)).locked() as archive:
        state = orchestration.current(store)
        terminal(state or {}, session)
        require(fence.read('active.json') == {'kind': 'state', 'directory': str(store.path)},
                'failed-run-active-directory-mismatch')
        require(fence.read('closed-' + session + '.json') is None and archive.read('review.json') is None and
                not any(n.endswith('.pending') for n in os.listdir(store.fd)), 'failed-run-already-reviewed-or-uncertain')
        control.check_all(fence, recovering=store.path)
        machine = factory(session)
        metadata = snapshot(machine, 'Running')
        before = observation(machine, state)
        consent(f'Phase: stop-failed-run; session: {session}; VM: {machine.name}.\n'
                'Stop ONLY this verified disposable VM, preserving its disk, volumes, journals, '
                'containers and failed report. No recovery replay, data deletion or retry. '
                'Record it as abandoned unsuccessful; a new VM requires separate approval.', f'stop-failed-run:{session}')
        require(orchestration.current(store) == state and snapshot(machine, 'Running') == metadata and
                observation(machine, state) == before, 'failed-run-observation-changed')
        archive.write('review.json', {'state': state, 'directory': str(store.path), 'observation': before,
                                     'metadata': metadata}, immutable=True)
        archive.write('stop.json', {'session': session, 'intent': 'stop-preserve-disk-no-replay'}, immutable=True)
        machine.command('stop', machine.name, timeout=120)
        require(snapshot(machine, 'Stopped') == metadata, 'failed-run-stopped-metadata-changed')
        consent(f'Phase: close-failed-run; session: {session}; VM: {machine.name} (Stopped).\n'
                'Close the host operation as abandoned failed. Preserve the original receipt, '
                'incomplete journal evidence and stopped VM; never resume this session.', f'close-failed-run:{session}')
        require(orchestration.current(store) == state and snapshot(machine, 'Stopped') == metadata,
                'failed-run-stopped-evidence-changed')
        decision = {'session': session, 'directory': str(store.path), 'receiptSha256': control.digest(state),
                    'decision': 'abandoned-failed-run-stopped-no-replay', 'recovery': str(archive.path),
                    'reviewSha256': control.digest(archive.read('review.json')), 'metadata': metadata}
        archive.write('closure.json', decision, immutable=True)
        fence.write('closed-' + session + '.json', decision, immutable=True)
        return {'status': 'passed', 'session': session, 'decision': decision['decision'],
                'realE2E': 'failed-run-preserved', 'vm': 'Stopped', 'journal': 'preserved-unresolved-no-replay'}


def verify_retained(decision):
    session = session_id(decision['session'])
    expected = control.root() / ('failed-' + session)
    require(decision['recovery'] == str(expected), 'failed-run-recovery-directory-changed')
    with Store(expected).locked() as archive, Store(decision['directory']).locked() as store:
        review, closure, stop = archive.read('review.json'), archive.read('closure.json'), archive.read('stop.json')
        state = orchestration.current(store)
        require(review is not None and closure == decision and stop == {
            'session': session, 'intent': 'stop-preserve-disk-no-replay'} and
            not any(n.endswith('.pending') for n in os.listdir(archive.fd)) and
            review['state'] == state and review['directory'] == str(store.path) and
            decision['receiptSha256'] == control.digest(state) and
            decision['reviewSha256'] == control.digest(review), 'failed-run-retained-evidence-changed')
        terminal(state, session)
