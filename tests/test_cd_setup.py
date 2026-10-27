"""Administrative installer filesystem integration inside a mapped disposable host."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import time
import io
import os
import stat
import zipfile

import pytest

from installation import cd_setup
from scripts.cd.package import export
from scripts.production_integration.operator_approval import canonical
from test_cd import identities, policy_document, signed_policy, REPO


def test_exported_capsule_loads_its_own_complete_caddy_inputs(host, tmp_path):
    import importlib.util
    from installation.operator_bootstrap import CADDY_INPUTS
    extracted = tmp_path / 'exported-capsule'
    with zipfile.ZipFile(host.base / 'cd-capsule.zip') as archive:
        for name in CADDY_INPUTS:
            assert archive.read('capsule/' + name) == (REPO / name).read_bytes()
            target = extracted / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read('capsule/' + name))
        source = extracted / 'scripts/caddy_producer.py'
        source.parent.mkdir(parents=True)
        source.write_bytes(archive.read('capsule/scripts/caddy_producer.py'))
    spec = importlib.util.spec_from_file_location('exported_caddy_producer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.INPUTS == extracted / 'images/caddy'
    assert module.load_lock()['version'] == '2.11.7'


@pytest.mark.parametrize('missing', ['inputs.lock.json', 'Dockerfile', 'github-trusted-root.jsonl'])
def test_capsule_export_rejects_missing_caddy_inputs(tmp_path, missing):
    from installation.operator_bootstrap import CADDY_INPUTS
    from scripts.docker_adapter import DockerError
    for name in CADDY_INPUTS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.name != missing: target.write_bytes((REPO / name).read_bytes())
    with pytest.raises(DockerError, match='cd-capsule-caddy-input-required'):
        export(tmp_path, assets={}, bindings=b'', external_pins={}, signed_policy=b'{}')


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
    assert 'operator-recovery' not in sudoers and 'operator-prebootstrap' not in sudoers and 'docker' not in sudoers
    assert 'operator-bootstrap-containment' not in sudoers
    assert 'operator-bootstrap-verification-recovery' not in sudoers
    assert 'operator-first-rollout-recovery' not in sudoers
    assert (cd_setup.CAPSULE/'scripts/cd/administrative.py').is_file()
    assert (cd_setup.CAPSULE/'scripts/cd/bootstrap_containment.py').is_file()
    assert (cd_setup.CAPSULE/'scripts/cd/bootstrap_recovery.py').is_file()
    assert (cd_setup.CAPSULE/'scripts/cd/rollout_recovery.py').is_file()
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


def rewrite_kit(host, mutate):
    path = host.base/'cd-capsule.zip'
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        files = {n: archive.read(n) for n in archive.namelist() if n != 'kit.json'}
    mutate(files)
    files['kit.json'] = canonical({'version': 1, 'files': {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, value in sorted(files.items()):
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (stat.S_IFREG|0o600) << 16
            archive.writestr(entry, value)
    path.write_bytes(out.getvalue())
    host.decision['kit_sha256'] = hashlib.sha256(out.getvalue()).hexdigest()
    (host.base/'cd-install-decision.json').write_bytes(canonical(host.decision))


@pytest.fixture
def capsule_update(host, monkeypatch):
    tool = host.root/'usr/bin/ssh-keygen'
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_bytes(b'fixed external tool')
    def external(files):
        d = json.loads(files['capsule/capsule.json'])
        d['files']['/usr/bin/ssh-keygen'] = hashlib.sha256(tool.read_bytes()).hexdigest()
        files['capsule/capsule.json'] = canonical(d)
    rewrite_kit(host, external)
    cd_setup.main()
    manager = host.root/'opt/portfolio-deployment'
    manager.mkdir(parents=True, mode=0o700)
    (manager/'app.py').write_bytes(b'# installed manager')
    (manager/'app.py').chmod(0o600)
    inventory = canonical({'manager_sha': 'a'*40, 'files': [
        ['/opt/portfolio-deployment/app.py', hashlib.sha256((manager/'app.py').read_bytes()).hexdigest()]]})
    (manager/'installation.json').write_bytes(inventory)
    (manager/'installation.json').chmod(0o600)
    capsule = cd_setup.CAPSULE
    host.decision.update(id='c'*32, action='replace-capsule',
        from_capsule_sha256=hashlib.sha256((capsule/'capsule.json').read_bytes()).hexdigest(),
        manager_inventory_sha256=hashlib.sha256(inventory).hexdigest(),
        policy_sha256=hashlib.sha256((host.root/'etc/portfolio-deployment/cd-policy.json').read_bytes()).hexdigest())
    def add(files):
        files['capsule/reviewed_extra.py'] = b'# independently reviewed new capsule capability'
        d = json.loads(files['capsule/capsule.json'])
        d['files']['/usr/local/lib/portfolio-deployment-cd/reviewed_extra.py'] = hashlib.sha256(files['capsule/reviewed_extra.py']).hexdigest()
        files['capsule/capsule.json'] = canonical(d)
    rewrite_kit(host, add)
    # Host ownership is synthetic; real tree closure/digests and exchange remain.
    original = Path.lstat
    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path.is_relative_to(host.root):
            values = list(info)
            values[4:6] = [0, 0]
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, 'lstat', metadata)
    return host


def test_exact_capsule_exchange_preserves_manager_policy_keys_and_lock(capsule_update):
    h = capsule_update
    paths = [h.root/n for n in ('opt/portfolio-deployment/installation.json', 'etc/portfolio-deployment/cd-policy.json',
                              'var/lib/portfolio-cd/.ssh/authorized_keys', 'etc/sudoers.d/portfolio-cd')]
    before = {p: p.read_bytes() for p in paths}
    inode = (h.root/'srv/portfolio/locks/host.lock').stat().st_ino
    cd_setup.main()
    assert (cd_setup.CAPSULE/'reviewed_extra.py').is_file()
    retained = cd_setup.CAPSULE.parent/('.portfolio-deployment-cd-'+'c'*32)
    assert retained.is_dir() and not (retained/'reviewed_extra.py').exists()
    assert {p: p.read_bytes() for p in paths} == before
    assert (h.root/'srv/portfolio/locks/host.lock').stat().st_ino == inode
    assert json.loads((h.base/('capsule-update-'+'c'*32+'.json')).read_bytes())['status'] == 'succeeded'


@pytest.mark.parametrize('failure', ['manager-changed', 'capsule-changed', 'external-tool-changed', 'ssh-policy-changed', 'expired'])
def test_capsule_replacement_rejects_drift_before_reservation(capsule_update, failure):
    h = capsule_update
    if failure == 'manager-changed':
        (h.root/'opt/portfolio-deployment/app.py').write_bytes(b'drift')
    elif failure == 'capsule-changed':
        (cd_setup.CAPSULE/'unreviewed.py').write_bytes(b'drift')
    elif failure == 'external-tool-changed':
        (h.root/'usr/bin/ssh-keygen').write_bytes(b'drift')
    elif failure == 'ssh-policy-changed':
        path = h.root/'etc/sudoers.d/portfolio-cd'
        path.chmod(0o600)
        path.write_bytes(b'drift')
    else:
        h.decision['expires'] = 1
        (h.base/'cd-install-decision.json').write_bytes(canonical(h.decision))
    with pytest.raises(ValueError): cd_setup.main()
    assert not (h.base/('cd-decision-'+'c'*32+'.used')).exists()


@pytest.mark.parametrize('after_exchange', [False, True])
def test_capsule_unknown_exchange_is_fenced_until_separate_restore(capsule_update, monkeypatch, after_exchange):
    h = capsule_update
    original = cd_setup.exchange
    def interrupt(*args):
        if after_exchange:
            original(*args)
        raise RuntimeError('unknown exchange')
    monkeypatch.setattr(cd_setup, 'exchange', interrupt)
    with pytest.raises(RuntimeError): cd_setup.main()
    h.decision['id'] = 'd'*32
    (h.base/'cd-install-decision.json').write_bytes(canonical(h.decision))
    with pytest.raises(ValueError): cd_setup.main()
    h.decision.update(action='restore-capsule', restore_id='c'*32,
                      from_capsule_sha256=hashlib.sha256((cd_setup.CAPSULE/'capsule.json').read_bytes()).hexdigest())
    (h.base/'cd-install-decision.json').write_bytes(canonical(h.decision))
    monkeypatch.setattr(cd_setup, 'exchange', original)
    cd_setup.main()
    assert not (cd_setup.CAPSULE/'reviewed_extra.py').exists()
    assert json.loads((h.base/('capsule-update-'+'c'*32+'.json')).read_bytes())['status'] == 'restored'
