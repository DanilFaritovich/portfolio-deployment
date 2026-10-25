#!/usr/bin/python3
"""Independent stdlib-only one-use administrative installation of a reviewed CD kit.

Install/review THIS helper through the existing administrative channel first.
It never starts a Manager, container or service and never touches /srv/portfolio.
The account must already be provisioned by the separately authorized host owner.
"""
import fcntl
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


def main():
    require(len(sys.argv) == 1 and os.geteuid() == 0 and sys.flags.isolated and sys.flags.no_site and
            sys.dont_write_bytecode and Path(__file__) == Path('/usr/local/lib/portfolio-deployment-bootstrap/cd_setup.py'))
    protected(__file__)
    for path in (BASE/'cd-install-decision.json', BASE/'cd-capsule.zip'):
        protected(path)
    decision = json.loads((BASE/'cd-install-decision.json').read_bytes())
    require(set(decision) == {'version','id','machine_id','kit_sha256','not_before','expires'} and
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
