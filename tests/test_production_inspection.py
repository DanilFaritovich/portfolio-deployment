"""Stage 09.2 uses synthetic inventories and fake transports, never real SSH."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yaml

from scripts.production import InspectionError
from scripts.production.inventory import load_inventory, Target, secure_file
from scripts.production.transport import Reply, local_check, ssh_argv, SSHTransport, run_bounded, MAX_OUTPUT
from scripts.production.probes import COMMANDS, normalize
from scripts.production.inspection import inspect
from scripts.production.report import load, save, validate, summary
from scripts.production import cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def forbid_real_ssh(monkeypatch):
    original = subprocess.Popen
    def guarded(argv, *args, **kwargs):
        assert Path(argv[0]).name not in ('ssh', 'ssh-keyscan'), 'Real SSH forbidden in offline tests'
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', guarded)


@pytest.fixture
def config(tmp_path):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    ssh = home / '.ssh'
    ssh.mkdir(mode=0o700)
    key = ssh / 'automation'
    key.touch(mode=0o600)  # Deliberately not a real private key; contents never read.
    trust = ssh / 'known_hosts'
    trust.write_text('[192.0.2.10]:2244 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n')
    trust.chmod(0o600)
    values = dict(ansible_host='192.0.2.10', ansible_port=2244, ansible_user='ansible',
                  ansible_private_key_file=str(key), bootstrap_login_user='root',
                  ansible_python_interpreter='/usr/bin/python3', swap_size_mb=2048,
                  ssh_listen_ports=[2244], ssh_verify_ports=[2244], firewall_allowed_tcp_ports=[80, 443])
    inventory = tmp_path / 'inventory.yml'

    def write(updated=None):
        inventory.write_text(yaml.safe_dump({'all': {'children': {'bootstrap': {'hosts': {'portfolio': updated or values}}}}}))
        return inventory
    write()
    return home, key, trust, values, inventory, write


def resource(name='foreign', **extra):
    return dict(name=name, managed='', application='', component='', project='', **extra)


def responses():
    from test_compose_version import HELP, HELP_SEPARATOR
    return {
        'os': b'ID=ubuntu\nVERSION_ID="24.04"\n', 'architecture': b'x86_64\n',
        'cpu': b'2\n', 'memory': b'4000000\n',
        'disk': b'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 40000000 1000 39000000 1% /\n',
        'hostname': b'synthetic-vds\n', 'machine_id': b'a' * 32 + b'\n',
        'engine': b'"28.0.1"\n', 'daemon': b'"SYNTHETIC-DAEMON-ID"\n', 'compose': b'v2.35.0\n',
        'compose_cli': (HELP_SEPARATOR + HELP_SEPARATOR.join(HELP)).encode(),
        'containers': b'', 'networks': b'NETWORKS\n', 'volumes': b'',
        'listeners': b'LISTEN 0 128 0.0.0.0:2244 0.0.0.0:*\n',
        'caddy': b'v2.10.2 h1:synthetic\n',
        'services': '\n\n'.join(f'Id={name}.service\nLoadState=loaded\nActiveState={"active" if name == "docker" else "inactive"}'
                                for name in ('caddy','nginx','apache2','docker')).encode(),
        'firewall': b'Status: inactive\n',
    }


class FakeTransport:
    def __init__(self, overrides=None):
        self.responses = responses() | (overrides or {})
        self.calls = []

    def probe(self, name):
        self.calls.append(name)
        value = self.responses[name]
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, Reply) else Reply(0, value)


def report(config, overrides=None):
    return inspect(load_inventory(config[4]), FakeTransport(overrides))


def check(document, name):
    return next(item for item in document['checks'] if item['name'] == name)


def test_synthetic_ubuntu_success(config):
    target = load_inventory(config[4])
    transport = FakeTransport()
    result = inspect(target, transport)
    validate(result)
    assert all(c['status'] == 'passed' for c in result['checks'])
    assert result['identity']['daemon'] == 'SYNTHETIC-DAEMON-ID'
    assert result['identity']['machine_id'] == 'a' * 32
    assert result['blockers']  # Offline/live diagnostics never establish deployment eligibility.
    assert transport.calls == list(COMMANDS)
    assert 'private_key' not in json.dumps(result)


def test_infrastructure_inventory(config, monkeypatch):
    home, key, trust, values, path, write = config
    monkeypatch.setenv('HOME', str(home))
    values['ansible_private_key_file'] = '~/.ssh/automation'
    target = load_inventory(write())
    assert (target.port, target.user, target.key) == (2244, 'ansible', key)
    assert target.user != values['bootstrap_login_user']
    assert local_check(target) == trust


@pytest.mark.parametrize('field,value', [
    ('ansible_port', 0), ('ansible_port', 65536), ('ansible_port', True), ('ansible_port', '2244'),
    ('ansible_user', 'root'), ('ansible_user', 'user;id'), ('ansible_user', '-oProxyCommand=id'),
    ('ansible_host', 'a$(id)'), ('ansible_host', 'host;id'), ('ansible_host', '-oProxyCommand=id'),
    ('ansible_host', 'a\nb'), ('ansible_host', 'foo:bad'),
    ('ansible_private_key_file', '/tmp/%h'), ('ansible_private_key_file', '/tmp/$(id)'),
    ('ansible_private_key_file', '/tmp/a\nb'), ('ansible_private_key_file', '/tmp/../secret'),
    ('ansible_ssh_common_args', '-oStrictHostKeyChecking=no'), ('ansible_password', 'secret'),
])
def test_reject_unsafe_inventory(config, field, value):
    config[3][field] = value
    with pytest.raises(InspectionError, match='invalid-inventory'):
        load_inventory(config[5]())


@pytest.mark.parametrize('raw', ['', 'all: []', 'all: {children: {bootstrap: {hosts: {}}}}',
                                 'all: &x {children: *x}', 'all: 1\nall: 2', 'all: !!python/object {}'])
def test_bad_yaml(config, raw):
    config[4].write_text(raw)
    with pytest.raises(InspectionError):
        load_inventory(config[4])


def test_missing_inventory(tmp_path):
    with pytest.raises(InspectionError):
        load_inventory(tmp_path / 'missing')


@pytest.mark.parametrize('problem', ['missing-key', 'key-mode', 'key-link', 'trust-mode', 'trust-link', 'unknown-trust', 'revoked-trust'])
def test_local_prerequisite_failures(config, problem):
    home, key, trust, values, path, write = config
    target = load_inventory(path)
    if problem == 'missing-key': key.unlink()
    if problem == 'key-mode': key.chmod(0o644)
    if problem == 'key-link':
        original = key.with_name('original'); key.rename(original); key.symlink_to(original)
    if problem == 'trust-mode': trust.chmod(0o666)
    if problem == 'trust-link':
        original = trust.with_name('original'); trust.rename(original); trust.symlink_to(original)
    if problem == 'unknown-trust': trust.write_text('other-host ssh-ed25519 AAAA\n')
    if problem == 'revoked-trust': trust.write_text('@revoked [192.0.2.10]:2244 ssh-ed25519 AAAA\n')
    with pytest.raises(InspectionError):
        local_check(target, trust)


def test_ssh_options(config):
    target = load_inventory(config[4])
    argv = ssh_argv(target, config[2])
    for option in ('StrictHostKeyChecking=yes', 'BatchMode=yes', 'IdentitiesOnly=yes',
                   'PreferredAuthentications=publickey', 'PasswordAuthentication=no',
                   'ForwardAgent=no', 'ClearAllForwardings=yes', 'PermitLocalCommand=no',
                   'ProxyCommand=none', 'ProxyJump=none', 'ControlPath=none', 'UpdateHostKeys=no'):
        assert option in argv
    assert argv[:4] == ['/usr/bin/ssh', '-F', '/dev/null', '-n']
    assert argv[-2:] == ['--', target.host]
    assert argv[argv.index('-p') + 1] == '2244'
    assert str(config[1]) in argv
    assert not any(option.endswith('=accept-new') for option in argv)


@pytest.mark.parametrize('stderr,code', [
    (b'Host key verification failed', 'host-key-mismatch'),
    (b'WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!', 'host-key-mismatch'),
    (b'Permission denied (publickey).', 'ssh-authentication-failed'),
    (b'Connection timed out', 'ssh-connection-timeout'),
    (b'Connection refused', 'ssh-network-or-transport-failure')])
def test_transport_errors(config, monkeypatch, stderr, code):
    monkeypatch.setattr('scripts.production.transport.local_check', lambda *a: config[2])
    runner = Mock(return_value=Reply(255, stderr=stderr))
    monkeypatch.setattr('scripts.production.transport.run_bounded', runner)
    transport = SSHTransport(load_inventory(config[4]), config[2])
    with pytest.raises(InspectionError, match=code):
        transport.probe('os')
    assert runner.call_count == 1
    assert runner.call_args.args[0][-1] == COMMANDS['os']


def test_transport_allowlist(config):
    transport = SSHTransport(load_inventory(config[4]), config[2])
    with pytest.raises(InspectionError, match='probe-not-allowed'):
        transport.probe('docker run malicious')


@pytest.mark.parametrize('code', ['host-key-mismatch', 'ssh-authentication-failed', 'transport-timeout',
                                  'ssh-network-or-transport-failure', 'transport-output-limit'])
def test_transport_failure_stops_connections(config, code):
    transport = FakeTransport({'daemon': InspectionError(code)})
    result = inspect(load_inventory(config[4]), transport)
    assert check(result, 'os')['status'] == 'passed'
    assert check(result, 'daemon')['status'] == 'blocked'
    assert check(result, 'compose')['status'] == 'unknown'
    assert transport.calls[-1] == 'daemon'
    validate(result)


@pytest.mark.parametrize('name', ['engine', 'daemon', 'containers', 'networks', 'volumes'])
def test_docker_permissions_not_absence(config, name):
    result = report(config, {name: Reply(1, stderr=b'permission denied while connecting to Docker daemon: SECRET')})
    assert check(result, name)['status'] != 'passed'
    assert check(result, name)['reason'] == 'docker-permission-denied'
    assert 'SECRET' not in json.dumps(result)
    assert check(result, 'listeners')['status'] == 'passed'


@pytest.mark.parametrize('name', ['engine', 'compose'])
def test_missing_docker_or_compose(config, name):
    result = report(config, {name: Reply(127, stderr=b'not found')})
    assert check(result, name)['status'] == 'blocked'


@pytest.mark.parametrize('kind', ['containers', 'networks', 'volumes'])
def test_foreign_resources_are_preserved(config, kind):
    extra = {'id': 'b'*64, 'state':'running', 'ports':''} if kind == 'containers' else {'driver':'bridge'}
    if kind == 'networks': extra.update(id='b'*64, ipam=[{'Subnet':'172.19.0.0/16'}])
    raw = (('NETWORKS\n' if kind == 'networks' else '') + json.dumps(resource(**extra))).encode()
    result = report(config, {kind:raw})
    item = check(result, kind)
    assert item['status'] == 'passed'
    assert item['details']['resources'][0]['ownership'] == 'foreign-or-unknown'
    validate(result)


@pytest.mark.parametrize('kind,name', [('networks','portfolio-proxy'), ('networks','portfolio-project-router-private'),
                                     ('containers','portfolio-project-router-web-1'), ('volumes','portfolio-project-router')])
def test_reserved_names_conflict(config, kind, name):
    extra = {'id':'c'*64, 'state':'running', 'ports':''} if kind == 'containers' else {'driver':'bridge'}
    if kind == 'networks': extra.update(id='c'*64, ipam=[{'Subnet':'172.20.0.0/16'}])
    raw = (('NETWORKS\n' if kind == 'networks' else '') + json.dumps(resource(name, **extra))).encode()
    result = report(config, {kind: raw})
    assert check(result, kind)['status'] == 'blocked'
    assert result['conflicts']


def test_overlap(config):
    raw = b'NETWORKS\n' + b'\n'.join(json.dumps(resource(name, id=character*64, driver='bridge', ipam=[{'Subnet':subnet}])).encode()
                                          for name, character, subnet in [('a','a','172.18.0.0/16'),('b','b','172.18.1.0/24')])
    assert check(report(config, {'networks':raw}), 'networks')['reason'] == 'subnet-overlap'


@pytest.mark.parametrize('port', [80,443])
def test_busy_public_ports(config, port):
    result = report(config, {'listeners': responses()['listeners'] + f'LISTEN 0 128 [::]:{port} [::]:*\n'.encode()})
    assert f'listeners:{port}:occupied' in result['conflicts']


@pytest.mark.parametrize('service', ['caddy','nginx','apache2'])
def test_existing_proxy(config, service):
    raw = responses()['services'].replace(f'Id={service}.service\nLoadState=loaded\nActiveState=inactive'.encode(),
                                          f'Id={service}.service\nLoadState=loaded\nActiveState=active'.encode())
    assert check(report(config, {'services':raw}), 'services')['status'] == 'blocked'


@pytest.mark.parametrize('name,raw', [('os',b'ID=ubuntu'), ('cpu',b'0'), ('memory',b'-1'),
                                    ('machine_id',b''), ('daemon',b'null'), ('engine',b'{}'),
                                    ('compose',b'1.29.0'), ('networks',b''), ('containers',b'{"Env":"SECRET"}'),
                                    ('listeners',b'garbage'), ('disk',b'Filesystem\n'), ('services',b''),
                                    ('firewall',b'ERROR permission denied'), ('hostname',b'\x1b[31msecret')])
def test_incomplete_probes_never_pass(config, name, raw):
    result = report(config, {name:raw})
    assert check(result, name)['status'] == 'unknown'
    assert check(result, 'architecture')['status'] == 'passed'
    assert check(result, name)['details'] == {}
    validate(result)


def test_report_private_atomic_roundtrip(config, tmp_path):
    destination = tmp_path / 'reports'
    result = report(config)
    path = save(result, destination)
    assert path.stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o700
    assert load(destination) == result
    assert 'blocker:' in summary(result)
    assert len(list(destination.iterdir())) == 1


@pytest.mark.parametrize('problem', ['missing', 'corrupt', 'version', 'symlink', 'mode', 'extra', 'incomplete'])
def test_report_rejects_unsafe_read(config, tmp_path, problem):
    root = tmp_path / 'reports'
    path = save(report(config), root)
    if problem == 'missing': path.unlink()
    if problem == 'corrupt': path.write_text('{')
    if problem == 'version':
        data = json.loads(path.read_text()); data['schemaVersion']='v999'; path.write_text(json.dumps(data))
    if problem == 'symlink':
        original=path.with_name('original'); path.rename(original); path.symlink_to(original)
    if problem == 'mode': path.chmod(0o644)
    if problem == 'extra':
        data=json.loads(path.read_text()); data['credentials']='secret'; path.write_text(json.dumps(data))
    if problem == 'incomplete':
        data=json.loads(path.read_text()); data['checks'].pop(); path.write_text(json.dumps(data))
    with pytest.raises(InspectionError): load(root)


def test_atomic_failure_preserves_previous_report(config, tmp_path, monkeypatch):
    root=tmp_path/'reports'; result=report(config); path=save(result,root); previous=path.read_bytes()
    def fail(*a): raise OSError('synthetic failure')
    monkeypatch.setattr('scripts.production.report.os.replace',fail)
    with pytest.raises(OSError): save(result,root)
    assert path.read_bytes() == previous
    assert list(root.iterdir()) == [path]


@pytest.mark.parametrize('answer', ['', 'yes', 'y', 'inspect', 'no'])
def test_confirmation_default_deny(config, monkeypatch, answer):
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    monkeypatch.setattr(sys, 'stdin', Mock(isatty=lambda:True))
    monkeypatch.setattr('builtins.input',lambda *a:answer)
    transport=Mock(side_effect=AssertionError('must not construct transport'))
    monkeypatch.setattr(cli,'SSHTransport',transport)
    assert cli.main(['inspect']) == 2
    transport.assert_not_called()


def test_noninteractive_denied(config, monkeypatch):
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    monkeypatch.setattr(sys,'stdin',io.StringIO('INSPECT\n'))
    monkeypatch.setattr(cli,'SSHTransport',Mock(side_effect=AssertionError('network forbidden')))
    assert cli.main(['inspect']) == 2


def test_confirmed_cli_fake_only(config, monkeypatch):
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    monkeypatch.setattr(sys,'stdin',Mock(isatty=lambda:True)); monkeypatch.setattr('builtins.input',lambda *a:'INSPECT')
    transport=FakeTransport(); monkeypatch.setattr(cli,'SSHTransport',lambda *a:transport)
    assert cli.main(['inspect']) == 2
    assert load()['identity']['daemon'] == 'SYNTHETIC-DAEMON-ID'
    assert cli.main(['report']) == 2


def test_offline_cli_never_ssh_or_network(config, monkeypatch):
    import socket
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    def forbidden(*a,**k): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket,'socket',forbidden)
    original = subprocess.Popen
    def guarded(argv,*a,**k):
        assert argv[0] == '/usr/bin/ssh-keygen' and argv[1] == '-F'
        return original(argv,*a,**k)
    monkeypatch.setattr(subprocess,'Popen',guarded)
    monkeypatch.setattr(cli,'SSHTransport',forbidden)
    assert cli.main(['check']) == 0
    assert cli.main(['report']) == 2


@pytest.mark.parametrize('target', ['production-check','production-report'])
def test_make_offline_interface(config, target):
    # No real inventory, keys or network. Absolute OpenSSH invocation is only
    # ssh-keygen -F reading the synthetic public trust file for production-check.
    env = {'PATH':os.environ['PATH'],'HOME':str(config[0]),'PRODUCTION_INVENTORY':str(config[4])}
    result=subprocess.run(['make',target,f'PYTHON={sys.executable}'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode == (0 if target == 'production-check' else 2)
    assert 'Target:' in result.stdout if target == 'production-check' else 'report-missing' in result.stderr


def test_commands_have_no_mutations_or_secrets():
    import re
    for command in COMMANDS.values():
        assert not re.search(r'\b(sudo|pull|create|restart|prune|reload|install|authorized_keys|ssh-keyscan)\b', command)
        assert '.Config.Env' not in command and 'systemctl start' not in command
    package='\n'.join(p.read_text() for p in (ROOT/'scripts/production').glob('*.py'))
    assert 'shell=True' not in package
    assert 'from scripts.deployment' not in package
    assert 'from scripts.e2e' not in package


@pytest.mark.parametrize('mode', ['output','timeout','interrupt'])
def test_bounded_runner_kills_on_failure(monkeypatch, mode):
    # Run a local Python fixture process only, never ssh. Covers actual pipe drain
    # and process cleanup semantics rather than only mocking subprocess.run.
    if mode == 'output':
        with pytest.raises(InspectionError,match='output-limit'):
            run_bounded([sys.executable,'-c',f'import os; os.write(1,b"a"*{MAX_OUTPUT+1})'])
    elif mode == 'timeout':
        with pytest.raises(InspectionError,match='timeout'):
            run_bounded([sys.executable,'-c','import time; time.sleep(2)'],timeout=0.05)
    else:
        import selectors
        monkeypatch.setattr(selectors.DefaultSelector,'select',Mock(side_effect=KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            run_bounded([sys.executable,'-c','import time; time.sleep(2)'])


def test_static_remote_commands_have_valid_shell_syntax():
    for name, command in COMMANDS.items():
        result = subprocess.run(['/bin/sh', '-n', '-c', command],capture_output=True,timeout=2)
        assert result.returncode == 0, name


def test_resource_null_fields_not_success():
    value = resource(id='a'*64, state=False, ports='')
    with pytest.raises(InspectionError): normalize('containers',json.dumps(value).encode())


def test_unsupported_os_architecture_and_missing_ssh_listener(config):
    result = report(config, {'os':b'ID=debian\nVERSION_ID="12"\n','architecture':b'aarch64','listeners':b''})
    assert all(check(result,name)['status'] == 'blocked' for name in ('os','architecture','listeners'))


def test_read_only_key_and_trust_unchanged(config):
    key, trust = config[1:3]
    before = (key.read_bytes(), trust.read_bytes(), key.stat().st_mtime_ns, trust.stat().st_mtime_ns)
    local_check(load_inventory(config[4]),trust)
    assert before == (key.read_bytes(), trust.read_bytes(), key.stat().st_mtime_ns, trust.stat().st_mtime_ns)


def test_no_private_key_read_by_local_check(config, monkeypatch):
    import builtins
    original = os.open
    def guarded(path, *args, **kwargs):
        assert str(path) != str(config[1]), 'private key must only be statted'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',guarded)
    local_check(load_inventory(config[4]),config[2])


def test_report_storage_inside_checkout_forbidden(config):
    with pytest.raises(InspectionError,match='outside-checkout'):
        save(report(config),ROOT/'reports')


def test_inspect_interrupt_before_connection(config, monkeypatch):
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    monkeypatch.setattr(sys,'stdin',Mock(isatty=lambda:True))
    monkeypatch.setattr('builtins.input',Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(cli,'SSHTransport',Mock(side_effect=AssertionError('network forbidden')))
    assert cli.main(['inspect']) == 130


def test_trust_fingerprint_uses_only_public_key(config):
    from scripts.production.transport import trust_fingerprints
    values = trust_fingerprints(load_inventory(config[4]),config[2])
    assert len(values) == 1 and values[0].startswith('SHA256:') and len(values[0]) == 50


def test_malformed_public_trust_fails_offline(config):
    config[2].write_text('[192.0.2.10]:2244 ssh-ed25519 AAAA\n')
    with pytest.raises(InspectionError,match='malformed-public-host-trust'):
        local_check(load_inventory(config[4]),config[2])


def test_changed_local_trust_stops_transport(config,monkeypatch):
    monkeypatch.setattr('scripts.production.transport.local_check',lambda *a:config[2])
    monkeypatch.setattr('scripts.production.transport.trust_fingerprints',lambda *a:['SHA256:changed'])
    runner=Mock(side_effect=AssertionError('SSH must not start'))
    monkeypatch.setattr('scripts.production.transport.run_bounded',runner)
    transport=SSHTransport(load_inventory(config[4]),config[2],['SHA256:initial'])
    with pytest.raises(InspectionError,match='host-key-mismatch'): transport.probe('os')
    runner.assert_not_called()


def test_report_unsafe_parent_rejected(config,tmp_path):
    parent=tmp_path/'shared'; parent.mkdir(mode=0o777); parent.chmod(0o777)
    with pytest.raises(InspectionError,match='unsafe-report-parent'):
        save(report(config),parent/'reports')


def test_make_offline_with_network_and_ssh_guards(config,tmp_path):
    wrapper=tmp_path/'guard.py'
    wrapper.write_text('''import sys, subprocess, socket, runpy
sys.path.insert(0, %r)
SocketType = socket.socket
class NetworkForbiddenSocket(SocketType):
    def __new__(cls, *a, **k):
        raise AssertionError("network forbidden")
socket.socket = NetworkForbiddenSocket
original = subprocess.Popen
def guarded(argv, *a, **k):
    assert argv[0] == "/usr/bin/ssh-keygen" and argv[1] == "-F", "SSH forbidden"
    return original(argv, *a, **k)
subprocess.Popen = guarded
module=sys.argv[2]
sys.argv=sys.argv[2:]
runpy.run_module(module,run_name="__main__")
''' % str(ROOT))
    env={'PATH':os.environ['PATH'],'HOME':str(config[0]),'PRODUCTION_INVENTORY':str(config[4])}
    for command in ('production-check','production-report'):
        result=subprocess.run(['make',command,f'PYTHON={sys.executable} {wrapper}'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
        assert result.returncode == (0 if command == 'production-check' else 2)
        assert 'AssertionError' not in result.stderr


def test_storage_preflight_is_offline_and_creates_nothing(config,monkeypatch):
    from scripts.production.report import storage_check
    monkeypatch.setenv('HOME',str(config[0]))
    expected=config[0]/'.local/share/portfolio-deployment/production-inspection'
    assert storage_check() == expected
    assert not (config[0]/'.local').exists()


def test_inspect_blocks_unsafe_storage_before_ssh(config,monkeypatch):
    monkeypatch.setenv('HOME',str(config[0])); monkeypatch.setenv('PRODUCTION_INVENTORY',str(config[4]))
    config[0].chmod(0o777)
    monkeypatch.setattr(cli,'SSHTransport',Mock(side_effect=AssertionError('SSH forbidden')))
    assert cli.main(['inspect']) == 2


@pytest.mark.parametrize('version', [b'2.0.0', b'v5.6.0', b'5.6.1', b'v5.7.0', b'6.0.0', b'v6.3.1'])
def test_modern_compose_versions_roundtrip_report_schema(config, version):
    result = report(config, {'compose': version})
    validate(result)
    assert check(result, 'compose')['status'] == 'passed'
    assert check(result, 'compose_cli')['status'] == 'passed'


@pytest.mark.parametrize('reply', [b'', Reply(1, b'', b'unavailable')])
def test_compose_cli_failure_remains_blocker(config, reply):
    result = report(config, {'compose_cli': reply})
    validate(result)
    assert check(result, 'compose')['status'] == 'passed'
    assert check(result, 'compose_cli')['status'] in ('unknown', 'blocked')
    assert any(value.startswith('compose_cli:') for value in result['blockers'])
