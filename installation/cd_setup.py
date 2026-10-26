#!/usr/bin/python3
"""Independent stdlib-only one-use administrative installation of a reviewed CD kit.

Install/review THIS helper through the existing administrative channel first.
It never starts a Manager, container or service and never touches /srv/portfolio.
The account must already be provisioned by the separately authorized host owner.
"""
import fcntl
import ctypes
import grp
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import sys
import time
import zipfile

BASE = Path('/var/lib/portfolio-deployment-bootstrap')
CAPSULE = Path('/usr/local/lib/portfolio-deployment-cd')


def require(value):
    if not value:
        raise ValueError('cd-administrative-installation-blocked')


def protected(path, directory=False):
    path = Path(path)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022 and
                (stat.S_ISDIR(info.st_mode) if parent != path or directory else
                 stat.S_ISREG(info.st_mode) and info.st_nlink == 1))


def exclusive(path, raw, mode=0o600):
    with os.fdopen(os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, mode), 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync(directory):
    fd = os.open(directory, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_record(path, record):
    pending = path.with_name(path.name + '.pending-' + os.urandom(8).hex())
    exclusive(pending, json.dumps(record, sort_keys=True, separators=(',', ':')).encode())
    os.replace(pending, path)
    sync(path.parent)


def exchange(first, second):
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.renameat2(-100, os.fsencode(first), -100, os.fsencode(second), 2) == 0)


def verify_inventory(root, *, manager=False, active_root=None):
    """No target imports: independently verify tree closure and external pins."""
    protected(root, directory=True)
    manifest = root / ('installation.json' if manager else 'capsule.json')
    protected(manifest)
    require(manifest.stat().st_size <= 2*1024*1024)
    raw = manifest.read_bytes()
    document = json.loads(raw)
    active_root = root if active_root is None else active_root
    pins = {str(Path(n)): h for n, h in dict(document['files']).items()}
    require(len(pins) == len(document['files']) and 0 < len(pins) <= 8192)
    actual, pending, total = set(), [root], 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o077)
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            if path != manifest:
                actual.add(str(active_root/path.relative_to(root)))
        require(len(actual) + len(pending) <= 8192)
    require(actual == {n for n in pins if Path(n).is_relative_to(active_root)})
    for name, digest in pins.items():
        path = Path(name)
        require(path.is_absolute() and '..' not in path.parts)
        if path.is_relative_to(active_root):
            path = root/path.relative_to(active_root)
        protected(path)
        size = path.stat().st_size
        total += size
        require(size <= 128*1024*1024 and total <= 512*1024*1024)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest)
    return raw, document


def fixed_transport(files):
    for path, name, mode in (
        (Path('/etc/portfolio-deployment/cd-policy.json'), 'policy.json', 0o600),
        (Path('/usr/local/bin/portfolio-cd-forced-command.py'), 'forced_command.py', 0o755),
        (Path('/etc/sudoers.d/portfolio-cd'), 'sudoers', 0o440),
        (Path('/var/lib/portfolio-cd/.ssh/authorized_keys'), 'authorized_keys', 0o644)):
        protected(path)
        require(path.read_bytes() == files[name] and stat.S_IMODE(path.stat().st_mode) == mode)


def update_capsule(decision, files):
    """Exact-kit administrative exchange; SSH policy and Manager remain fixed."""
    require(decision['action'] == 'replace-capsule')
    root = Path('/opt/portfolio-deployment')
    installed, _ = verify_inventory(root, manager=True)
    require(hashlib.sha256(installed).hexdigest() == decision['manager_inventory_sha256'])
    policy = Path('/etc/portfolio-deployment/cd-policy.json')
    protected(policy)
    require(hashlib.sha256(policy.read_bytes()).hexdigest() == decision['policy_sha256'])
    old_raw, old = verify_inventory(CAPSULE)
    require(hashlib.sha256(old_raw).hexdigest() == decision['from_capsule_sha256'])
    new_raw = files['capsule/capsule.json']
    new = json.loads(new_raw)
    internal = {str(CAPSULE/n.removeprefix('capsule/')): hashlib.sha256(v).hexdigest()
                for n, v in files.items() if n.startswith('capsule/') and n != 'capsule/capsule.json'}
    require({str(Path(n)): h for n, h in new['files'].items() if Path(n).is_relative_to(CAPSULE)} == internal)
    require({n: h for n, h in new['files'].items() if not Path(n).is_relative_to(CAPSULE)} ==
            {n: h for n, h in old['files'].items() if not Path(n).is_relative_to(CAPSULE)})
    lock = Path('/srv/portfolio/locks/host.lock')
    protected(lock)
    with lock.open('rb') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX|fcntl.LOCK_NB)
        require((os.fstat(lease.fileno()).st_dev, os.fstat(lease.fileno()).st_ino) ==
                (lock.stat().st_dev, lock.stat().st_ino))
        records = list(BASE.glob('capsule-update-*.json'))
        require(len(records) <= 10000)
        for path in records:
            protected(path)
            require(json.loads(path.read_bytes())['status'] in ('succeeded', 'restored'))
        require(verify_inventory(CAPSULE)[0] == old_raw and verify_inventory(root, manager=True)[0] == installed)
        fixed_transport(files)
        require(decision['not_before'] <= time.time() < decision['expires'])
        exclusive(BASE/('cd-decision-'+decision['id']+'.used'), decision['kit_sha256'].encode())
        receipt = BASE/('capsule-update-'+decision['id']+'.json')
        retained = CAPSULE.parent / ('.portfolio-deployment-cd-' + decision['id'])
        require(not retained.exists() and not retained.is_symlink())
        record = {'status': 'staging', 'id': decision['id'], 'decision': decision,
                  'old_manifest': hashlib.sha256(old_raw).hexdigest(),
                  'new_manifest': hashlib.sha256(new_raw).hexdigest(), 'retained': retained.name}
        exclusive(receipt, json.dumps(record, sort_keys=True).encode())
        retained.mkdir(mode=0o700)
        for name, value in sorted(files.items()):
            if not name.startswith('capsule/'):
                continue
            target = retained/name.removeprefix('capsule/')
            for parent in reversed(target.relative_to(retained).parents):
                (retained/parent).mkdir(mode=0o700, exist_ok=True)
            exclusive(target, value)
        require(verify_inventory(retained, active_root=CAPSULE)[0] == new_raw)
        require(verify_inventory(CAPSULE)[0] == old_raw and verify_inventory(root, manager=True)[0] == installed)
        fixed_transport(files)
        require(policy.read_bytes() == files['policy.json'] and time.time() < decision['expires'])
        sync(retained)
        record['status'] = 'switch-intent'
        replace_record(receipt, record)
        exchange(CAPSULE, retained)
        sync(CAPSULE.parent)
        require(verify_inventory(CAPSULE)[0] == new_raw)
        record['status'] = 'succeeded'
        replace_record(receipt, record)
    print('{"status":"capsule-updated","executorStarted":false}')


def restore_capsule(decision):
    """Separate root decision resolves recorded state; never reruns an update."""
    require(re.fullmatch('[0-9a-f]{32}', decision['restore_id']))
    receipt = BASE/('capsule-update-'+decision['restore_id']+'.json')
    protected(receipt)
    require(receipt.stat().st_size <= 16384)
    record = json.loads(receipt.read_bytes())
    require(record['id'] == decision['restore_id'] and record['status'] != 'restored')
    retained = CAPSULE.parent/('.portfolio-deployment-cd-'+decision['restore_id'])
    require(record['retained'] == retained.name)
    lock = Path('/srv/portfolio/locks/host.lock')
    protected(lock)
    with lock.open('rb') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX|fcntl.LOCK_NB)
        require((os.fstat(lease.fileno()).st_dev, os.fstat(lease.fileno()).st_ino) ==
                (lock.stat().st_dev, lock.stat().st_ino))
        active = verify_inventory(CAPSULE)[0]
        require(hashlib.sha256(active).hexdigest() == decision['from_capsule_sha256'])
        manager = verify_inventory(Path('/opt/portfolio-deployment'), manager=True)[0]
        require(hashlib.sha256(manager).hexdigest() == decision['manager_inventory_sha256'])
        policy = Path('/etc/portfolio-deployment/cd-policy.json')
        protected(policy)
        require(hashlib.sha256(policy.read_bytes()).hexdigest() == decision['policy_sha256'])
        need_swap = hashlib.sha256(active).hexdigest() == record['new_manifest']
        if need_swap:
            old = verify_inventory(retained, active_root=CAPSULE)[0]
            require(hashlib.sha256(old).hexdigest() == record['old_manifest'])
        else:
            require(hashlib.sha256(active).hexdigest() == record['old_manifest'])
        require(decision['not_before'] <= time.time() < decision['expires'])
        exclusive(BASE/('cd-decision-'+decision['id']+'.used'), decision['kit_sha256'].encode())
        recovery = BASE/('capsule-restoration-'+decision['id']+'.json')
        operation = {'status': 'restore-intent', 'decision': decision, 'original': record}
        exclusive(recovery, json.dumps(operation, sort_keys=True).encode())
        if need_swap:
            exchange(CAPSULE, retained)
            sync(CAPSULE.parent)
        require(hashlib.sha256(verify_inventory(CAPSULE)[0]).hexdigest() == record['old_manifest'])
        record['prior_status'] = record['status']
        record['status'] = 'restored'
        record['restoration_id'] = decision['id']
        replace_record(receipt, record)
        operation['status'] = 'restored'
        replace_record(recovery, operation)
    print('{"status":"capsule-restored","executorStarted":false}')


def main():
    require(len(sys.argv) == 1 and os.geteuid() == 0 and sys.flags.isolated and sys.flags.no_site and
            sys.dont_write_bytecode and Path(__file__) == Path('/usr/local/lib/portfolio-deployment-bootstrap/cd_setup.py'))
    protected(__file__)
    for path in (BASE/'cd-install-decision.json', BASE/'cd-capsule.zip'):
        protected(path)
    decision = json.loads((BASE/'cd-install-decision.json').read_bytes())
    fields = {'version','id','machine_id','kit_sha256','not_before','expires'}
    update_fields = {'action', 'from_capsule_sha256', 'manager_inventory_sha256', 'policy_sha256'}
    require(set(decision) in (fields, fields | update_fields, fields | update_fields | {'restore_id'}) and
            decision['version'] == 1 and re.fullmatch('[0-9a-f]{32}',decision['id']) and
            decision['machine_id'] == Path('/etc/machine-id').read_text().strip() and
            type(decision['not_before']) is int and type(decision['expires']) is int and
            decision['not_before'] <= time.time() < decision['expires'] and
            0 < decision['expires'] - decision['not_before'] <= 3600)
    require((BASE/'cd-capsule.zip').stat().st_size <= 128*1024*1024)
    raw = (BASE/'cd-capsule.zip').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == decision['kit_sha256'])
    account = pwd.getpwnam('portfolio-cd')
    require(account.pw_uid >= 1000 and account.pw_dir == '/var/lib/portfolio-cd' and
            not any(g.gr_name in ('docker','sudo','root') and
                    (g.gr_gid == account.pw_gid or account.pw_name in g.gr_mem) for g in grp.getgrall()))
    protected('/etc/shadow')
    require(Path('/etc/shadow').stat().st_size<=1024*1024)
    shadow=[line.split(':',2)[1] for line in Path('/etc/shadow').read_text().splitlines()
            if line.startswith('portfolio-cd:')]
    require(len(shadow)==1 and shadow[0].startswith(('!','*')))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 8192 and len({e.filename for e in entries}) == len(entries) and
                sum(e.file_size for e in entries) <= 128*1024*1024)
        files = {}
        for e in entries:
            p = PurePosixPath(e.filename)
            require(not p.is_absolute() and '..' not in p.parts and str(p) == e.filename and
                    re.fullmatch('[A-Za-z0-9_./-]+',e.filename) and not e.flag_bits & 1 and
                    e.compress_type == zipfile.ZIP_STORED and stat.S_IFMT(e.external_attr>>16) == stat.S_IFREG)
            files[e.filename] = archive.read(e)
    manifest = json.loads(files.pop('kit.json'))
    require(set(manifest) == {'version','files'} and manifest['version'] == 1 and
            {n:hashlib.sha256(v).hexdigest() for n,v in files.items()} == manifest['files'])
    required = {'capsule/capsule.json','capsule/cd_launcher.py','capsule/cd_bindings.py',
                'policy.json','forced_command.py','authorized_keys','sudoers'}
    require(required <= files.keys() and all(n.startswith('capsule/') or n in required for n in files))
    targets = {CAPSULE/n.removeprefix('capsule/'): (v,0o600) for n,v in files.items() if n.startswith('capsule/')}
    targets.update({Path('/etc/portfolio-deployment/cd-policy.json'):(files['policy.json'],0o600),
                    Path('/usr/local/bin/portfolio-cd-forced-command.py'):(files['forced_command.py'],0o755),
                    Path('/etc/sudoers.d/portfolio-cd'):(files['sudoers'],0o440),
                    Path('/var/lib/portfolio-cd/.ssh/authorized_keys'):(files['authorized_keys'],0o644)})
    if 'action' in decision:
        require((decision['action'] == 'replace-capsule' and 'restore_id' not in decision) or
                (decision['action'] == 'restore-capsule' and 'restore_id' in decision))
        for path, (data, mode) in targets.items():
            if not path.is_relative_to(CAPSULE):
                protected(path)
                require(path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) == mode)
        if decision['action'] == 'restore-capsule':
            restore_capsule(decision)
        else:
            update_capsule(decision, files)
        return
    state=Path('/var/lib/portfolio-deployment-cd')
    marker=state/'.cd-state-v1'
    if state.exists() or state.is_symlink():
        protected(state,directory=True)
        if any(state.iterdir()):
            protected(marker)
            require(marker.read_bytes()==b'portfolio-deployment:cd-state-v1\n')
    if CAPSULE.exists() or CAPSULE.is_symlink():
        protected(CAPSULE,directory=True)
        require(all(p in targets for p in CAPSULE.rglob('*') if not p.is_dir()))
    for path,(data,mode) in targets.items():
        if path.exists() or path.is_symlink():
            protected(path)
            require(path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) == mode)
    # Syntax-check only; this is not a sudo relay and performs no live deployment.
    sudoers = BASE/'cd-reviewed-sudoers'
    if sudoers.exists():
        protected(sudoers)
        require(sudoers.read_bytes() == files['sudoers'])
    else:
        exclusive(sudoers,files['sudoers'])
    require(subprocess.run(['/usr/sbin/visudo','-cf',str(sudoers)],stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=5,check=False).returncode == 0)
    lock = Path('/srv/portfolio/locks/host.lock')
    protected(lock)
    with lock.open('rb') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require((os.fstat(lease.fileno()).st_dev,os.fstat(lease.fileno()).st_ino) ==
                (lock.stat().st_dev,lock.stat().st_ino))
        exclusive(BASE/('cd-decision-'+decision['id']+'.used'),decision['kit_sha256'].encode())
        for directory in (CAPSULE,Path('/var/lib/portfolio-deployment-cd'),Path('/var/lib/portfolio-deployment-cd/requests'),
                          Path('/var/lib/portfolio-deployment-public'),Path('/var/lib/portfolio-cd/.ssh')):
            if not directory.exists():
                protected(directory.parent,directory=True)
                directory.mkdir(mode=0o700)
            protected(directory,directory=True)
        for path,(data,mode) in sorted(targets.items()):
            for parent in reversed(path.parents):
                if not parent.exists():
                    parent.mkdir(mode=0o700)
                protected(parent,directory=True)
            if not path.exists():
                exclusive(path,data,mode)
        if not marker.exists():
            exclusive(marker,b'portfolio-deployment:cd-state-v1\n')
    print('{"status":"cd-installed","executorStarted":false}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"blocked","executorStarted":false}')
        sys.exit(2)
