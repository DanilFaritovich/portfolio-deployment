"""Administrative installer filesystem integration inside a mapped disposable host."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import time

import pytest

from installation import cd_setup
from scripts.cd.package import export
from scripts.production_integration.operator_approval import canonical
from test_cd import identities, policy_document, signed_policy, REPO


@pytest.fixture
def host(tmp_path,monkeypatch,identities):
    root=tmp_path/'host'
    root.mkdir(mode=0o700)
    def mapped(value):
        path=Path(value)
        if path.is_relative_to(root):return path
        return root/path.relative_to('/') if path.is_absolute() else path
    for directory in ('var/lib/portfolio-deployment-bootstrap','var/lib/portfolio-cd','etc/portfolio-deployment',
                      'srv/portfolio/locks','usr/local/lib/portfolio-deployment-bootstrap'):
        (root/directory).mkdir(parents=True,exist_ok=True,mode=0o700)
    (root/'srv/portfolio/locks/host.lock').write_bytes(b'')
    (root/'etc/machine-id').write_text('a'*32+'\n')
    (root/'etc/shadow').write_text('portfolio-cd:!:10000:0:99999:7:::\n')
    helper='/usr/local/lib/portfolio-deployment-bootstrap/cd_setup.py'
    mapped(helper).write_bytes(b'independently reviewed helper')
    policy=signed_policy(policy_document(identities),identities)
    kit=export(REPO,assets={'vendor/library.py':b'# reviewed dependency','tools/caddy':b'# pinned tool'},
        bindings=b'POLICY=None\n',external_pins={'/usr/bin/ssh-keygen':'a'*64},signed_policy=policy.raw)
    base=root/'var/lib/portfolio-deployment-bootstrap'
    (base/'cd-capsule.zip').write_bytes(kit)
    decision={'version':1,'id':'b'*32,'machine_id':'a'*32,'kit_sha256':hashlib.sha256(kit).hexdigest(),
              'not_before':int(time.time())-1,'expires':int(time.time())+300}
    (base/'cd-install-decision.json').write_bytes(canonical(decision))
    def protected(path,directory=False):
        path=Path(path)
        if path not in root.parents:
            path=mapped(path)
        assert path.is_relative_to(root) or path in root.parents
        if not path.exists() or path.is_symlink():
            raise ValueError('unavailable-disposable-host-path')
        assert path.is_dir() if directory else path.is_file()
    monkeypatch.setattr(cd_setup,'Path',mapped)
    monkeypatch.setattr(cd_setup,'BASE',base)
    monkeypatch.setattr(cd_setup,'CAPSULE',root/'usr/local/lib/portfolio-deployment-cd')
    monkeypatch.setattr(cd_setup,'__file__',helper)
    monkeypatch.setattr(cd_setup,'sys',SimpleNamespace(argv=['setup'],flags=SimpleNamespace(isolated=True,no_site=True),dont_write_bytecode=True))
    monkeypatch.setattr(cd_setup,'protected',protected)
    monkeypatch.setattr(cd_setup.os,'geteuid',lambda:0)
    monkeypatch.setattr(cd_setup.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=1001,pw_gid=1001,
        pw_dir='/var/lib/portfolio-cd',pw_name=name))
    monkeypatch.setattr(cd_setup.grp,'getgrall',lambda:[])
    commands=[]
    def command(args,**kwargs):
        commands.append(args)
        assert args[:2]==['/usr/sbin/visudo','-cf']
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cd_setup.subprocess,'run',command)
    return SimpleNamespace(root=root,base=base,decision=decision,commands=commands)


def test_fixed_installation_replay_and_no_runtime_activation(host):
    lock=host.root/'srv/portfolio/locks/host.lock'
    before=lock.stat().st_ino
    cd_setup.main()
    assert lock.stat().st_ino==before
    assert host.commands and all(c[0]=='/usr/sbin/visudo' for c in host.commands)
    sudoers=(host.root/'etc/sudoers.d/portfolio-cd').read_text()
    assert 'operator-recovery' not in sudoers and 'docker' not in sudoers
    keys=(host.root/'var/lib/portfolio-cd/.ssh/authorized_keys').read_text().splitlines()
    assert len(keys)==2 and all(k.startswith('restrict,command=') for k in keys)
    assert (host.root/'var/lib/portfolio-deployment-cd/.cd-state-v1').is_file()
    with pytest.raises(FileExistsError):cd_setup.main()
    assert lock.stat().st_ino==before


@pytest.mark.parametrize('failure',['expired','foreign-state','docker-account','foreign-capsule','password'])
def test_unsafe_initial_setup_is_rejected_before_authorized_keys(host,monkeypatch,failure):
    if failure=='expired':
        host.decision['expires']=1
        (host.base/'cd-install-decision.json').write_bytes(canonical(host.decision))
    elif failure=='foreign-state':
        state=host.root/'var/lib/portfolio-deployment-cd'
        state.mkdir(mode=0o700)
        (state/'foreign-data').write_bytes(b'preserve')
    elif failure=='foreign-capsule':
        cd_setup.CAPSULE.mkdir(mode=0o700)
        (cd_setup.CAPSULE/'unreviewed.py').write_bytes(b'raise RuntimeError()')
    elif failure=='docker-account':
        monkeypatch.setattr(cd_setup.grp,'getgrall',lambda:[SimpleNamespace(gr_name='docker',gr_gid=999,gr_mem=['portfolio-cd'])])
    else:
        (host.root/'etc/shadow').write_text('portfolio-cd:enabled-test-password:1:0:99999:7:::\n')
    with pytest.raises(ValueError):cd_setup.main()
    assert not (host.root/'var/lib/portfolio-cd/.ssh/authorized_keys').exists()
    assert not (host.root/'etc/sudoers.d/portfolio-cd').exists()
