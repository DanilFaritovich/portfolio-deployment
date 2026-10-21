"""Single-use recovery of a lost confirmation AFTER successful immutable pulls.

No PTY writes, SIGKILL, phase replay, image rebuilds or original receipt edits.
SIGINT uses a pidfd and the old controller's existing interruption handler.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import signal

from scripts.docker_adapter import DockerError
from scripts.e2e import control, orchestration
from scripts.e2e.host_state import Store
from scripts.e2e.paths import directory
from scripts.e2e.safety import require, session_id
from scripts.e2e.vm import VM, ROOT
from scripts.e2e.multipass import evidence

PROC = Path('/proc')


@contextmanager
def read_store(path):
    # The old controller holds this lock. Read through the same private/bounded
    # file validator, then recheck exact bytes around confirmation and SIGINT.
    with directory(Path(path).absolute()) as descriptor:
        store = Store(path)
        store.fd = descriptor
        yield store


def prepared(state, session):
    require(state is not None and state['session'] == session and state['phase'] == 'images' and
            state['status'] == 'planned' and state['mutationStarted'] is True and
            state['result'] == {'status': 'passed', 'images': 6, 'realE2E': 'not-performed'} and
            state['identity'] is not None, 'recovery-requires-completed-images-unstarted-run')


def process(pid, path):
    require(type(pid) is int and pid > 1 and pid != os.getpid(), 'recovery-invalid-controller-pid')
    proc = PROC / str(pid)
    require(proc.stat().st_uid == os.geteuid(), 'recovery-controller-owner-mismatch')
    command = (proc / 'cmdline').read_bytes().split(b'\0')[:-1]
    require(len(command) == 4 and command[1:] == [b'-m', b'scripts.e2e.cli', b'run'] and
            (ROOT / os.fsdecode(command[0])).resolve() == (ROOT / '.venv/bin/python').resolve() and
            (proc / 'cwd').resolve() == ROOT, 'recovery-controller-command-mismatch')
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    require(fields[0] == 'S' and not (proc / 'task' / str(pid) / 'children').read_text().strip() and
            len(list((proc / 'task').iterdir())) == 1 and
            (proc / 'wchan').read_text().strip() == 'wait_woken', 'recovery-controller-not-idle')
    tty = str((proc / 'fd/0').readlink())
    require(tty.startswith('/dev/pts/') and str((proc / 'fd/1').readlink()) == tty,
            'recovery-controller-not-interactive')
    # Corroborate that THIS process owns both persistent controller flocks.
    held = set()
    for fd in (proc / 'fd').iterdir():
        try:
            info = (proc / 'fdinfo' / fd.name).read_text()
            if f'FLOCK  ADVISORY  WRITE {pid} ' in info or f'FLOCK  ADVISORY   WRITE {pid} ' in info:
                held.add((fd.stat().st_dev, fd.stat().st_ino))
        except FileNotFoundError:
            continue
    for lock in (control.root() / 'controller.lock', Path(path) / 'controller.lock'):
        value = lock.stat()
        require((value.st_dev, value.st_ino) in held, 'recovery-controller-lock-owner-mismatch')
    return {'pid': pid, 'start': fields[19], 'tty': tty, 'command': [os.fsdecode(v) for v in command]}


def guest_unstarted(machine, state):
    require(orchestration.identity(machine) == state['identity'], 'recovery-vm-identity-changed')
    report = machine.invoke('diagnose', f'diagnose:{machine.session}')
    require(report.get('status') == 'passed' and report.get('report') == {'realE2E': 'not-performed'},
            'recovery-guest-already-started')
    # A crash before report creation is NOT proof of an unstarted run. Require
    # the pristine session directory (bootstrap creates registry before mutation).
    names = json.loads(machine.guest('/opt/portfolio-e2e/.venv/bin/python', '-c',
        'import json,os; print(json.dumps(sorted(os.listdir('
        f'"/var/lib/portfolio-e2e/{machine.session}"))))'))
    require(names == ['identity.json'], 'recovery-guest-evidence-not-pristine')


def stop(path, session, pid, *, consent=orchestration.confirm, factory=VM):
    session_id(session)
    recovery = control.root() / ('prepared-' + session)
    with Store(recovery).locked() as archive:
        require(archive.read('stop.json') is None, 'recovery-stop-already-attempted')
        # pidfd pins process identity even if the numeric PID is later reused.
        handle = os.pidfd_open(pid)
        try:
            with read_store(path) as store, read_store(control.root()) as fence:
                state = orchestration.current(store)
                prepared(state, session)
                require(fence.read('active.json') == {'kind': 'state', 'directory': str(store.path)},
                        'recovery-active-directory-mismatch')
                before = process(pid, store.path)
                guest_unstarted(factory(session), state)
                consent(f'Phase: interrupt-waiting-controller; session: {session}; PID: {pid}; terminal: {before["tty"]}.\n'
                        'Send SIGINT only to the pinned idle controller. Preserve VM, images, locks and receipts; '
                        'its existing handler records interruption. No run is authorized.', f'interrupt:{session}')
                require(orchestration.current(store) == state and process(pid, store.path) == before,
                        'recovery-controller-changed')
                guest_unstarted(factory(session), state)
                archive.write('stop.json', {'version': 1, 'directory': str(store.path), 'session': session,
                              'before': state, 'process': before}, immutable=True)
                signal.pidfd_send_signal(handle, signal.SIGINT)
                require(bool(select.select([handle], [], [], 15)[0]), 'recovery-controller-exit-unconfirmed')
        finally:
            os.close(handle)
        with Store(control.root()).locked() as fence, Store(path).locked() as store:
            after = orchestration.current(store)
            interrupted(after, state)
            guest_unstarted(factory(session), state)
            archive.write('stopped.json', {'receipt': after, 'receiptSha256': control.digest(after)}, immutable=True)
        return {'status': 'passed', 'session': session, 'controller': 'interrupted', 'realE2E': 'not-performed',
                'evidence': str(recovery), 'next': 'make e2e-continue-prepared with same session/state directory'}


def interrupted(after, before):
    expected = {**before, 'status': 'blocked', 'result': {
        'reason': 'e2e-interrupted-or-unconfirmed', 'realE2E': 'not-performed'}}
    require(after == expected, 'recovery-controller-outcome-uncertain')


def continue_prepared(path, session, *, consent=orchestration.confirm, factory=VM):
    session_id(session)
    with Store(control.root()).locked() as fence, Store(path).locked() as store, \
            Store(control.root() / ('prepared-' + session)).locked() as archive:
        proof, stopped = archive.read('stop.json'), archive.read('stopped.json')
        require(proof is not None and stopped is not None and proof['directory'] == str(store.path) and
                proof['session'] == session, 'recovery-stop-proof-required')
        state = orchestration.current(store)
        prepared(proof['before'], session)
        interrupted(state, proof['before'])
        require(stopped == {'receipt': state, 'receiptSha256': control.digest(state)}, 'recovery-receipt-changed')
        require(fence.read('active.json') == {'kind': 'state', 'directory': str(store.path)},
                'recovery-active-directory-mismatch')
        require(archive.read('run.json') is None and not any(n.endswith('.pending') for n in os.listdir(archive.fd)),
                'recovery-run-already-attempted')
        control.check_all(fence, recovering=store.path)
        require(not any(n.endswith('.pending') for n in os.listdir(store.fd)), 'e2e-pending-evidence-review-required')
        machine = factory(session)
        pinned = store.path / session / 'lock.json'
        require(orchestration.image_fingerprint(pinned) == state['images'], 'recovery-images-changed')
        guest_unstarted(machine, state)
        for phase in ('run', 'activate'):
            consent(f'Phase: {phase}; prepared session: {session}; VM: {machine.name}.\n'
                    'Continue only the never-started real scenarios with preloaded immutable images. '
                    'Authorize VM Docker scenarios / readiness-gated loopback Caddy activation; no rebuild or pull.',
                    f'{phase}:{session}')
            require(orchestration.current(store) == state and
                    orchestration.image_fingerprint(pinned) == state['images'], 'recovery-inputs-changed')
            guest_unstarted(machine, state)
        attempt = {'version': 1, 'session': session, 'status': 'in-progress', 'phase': 'run',
                   'originalReceiptSha256': control.digest(state), 'result': {}}
        archive.write('run.json', attempt)  # Intent BEFORE dispatch. Never replay.
        try:
            result = machine.invoke('run', f'run:{session}', f'activate:{session}')
            require(result.get('status') in ('passed', 'blocked', 'failed'), 'invalid-e2e-phase-result')
            attempt.update(status=result['status'], result=result)
        except BaseException as error:
            attempt.update(status='unknown', result={'reason': error.code if isinstance(error, DockerError) else
                           'e2e-interrupted-or-unconfirmed', **evidence(error)})
            archive.write('run.json', attempt)
            raise
        archive.write('run.json', attempt)
        if attempt['status'] == 'passed':
            from scripts.e2e.runner import SCENARIOS
            require(result.get('mode') == 'real' and result.get('realE2E') == 'performed' and
                    [r['scenario'] for r in result.get('scenarios', [])] == list(SCENARIOS) and
                    all(r['status'] == 'passed' for r in result['scenarios']), 'recovery-incomplete-real-report')
            fence.write('closed-' + session + '.json', {'session': session, 'directory': str(store.path),
                        'receiptSha256': control.digest(state), 'decision': 'completed-prepared-run-no-replay',
                        'recovery': str(archive.path), 'runSha256': control.digest(attempt)}, immutable=True)
        return attempt


def verify_retained(decision):
    """A successful continuation never permits deleting its evidence to bypass fences."""
    session = session_id(decision['session'])
    expected = control.root() / ('prepared-' + session)
    require(decision['recovery'] == str(expected), 'recovery-evidence-directory-changed')
    with Store(expected).locked() as archive, Store(decision['directory']).locked() as store:
        proof, stopped, attempt = archive.read('stop.json'), archive.read('stopped.json'), archive.read('run.json')
        state = orchestration.current(store)
        require(proof is not None and stopped is not None and attempt is not None and
                proof['directory'] == str(store.path) and proof['session'] == session,
                'recovery-retained-evidence-missing')
        prepared(proof['before'], session)
        interrupted(state, proof['before'])
        require(stopped == {'receipt': state, 'receiptSha256': control.digest(state)} and
                decision['receiptSha256'] == control.digest(state) and attempt['status'] == 'passed' and
                attempt['originalReceiptSha256'] == control.digest(state) and
                decision['runSha256'] == control.digest(attempt) and
                not any(n.endswith('.pending') for n in os.listdir(archive.fd)), 'recovery-retained-evidence-changed')
