"""Redacted client failures and configuration gates; never contact SSH or HTTPS."""
import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts.cd import runner, bootstrap_runner
from scripts.docker_adapter import DockerError


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name, value in {'CD_HOST':'example.invalid','CD_USER':'portfolio-cd',
        'CD_KNOWN_HOSTS_B64':base64.b64encode(b'test-only pinned hosts').decode(),
        'CD_AUTOMATION_KEY_B64':base64.b64encode(b'test-only automation key').decode(),
        'CD_OBSERVER_KEY_B64':base64.b64encode(b'test-only observer key').decode()}.items():
        monkeypatch.setenv(name,value)
    return runner.Client(tmp_path)


@pytest.mark.parametrize('stderr,reason', [
    (b'Permission denied (publickey).','cd-ssh-authentication-failed'),
    (b'Host key verification failed.','cd-ssh-host-key-mismatch'),
    (b'REMOTE HOST IDENTIFICATION HAS CHANGED!','cd-ssh-host-key-mismatch'),
    (b'Connection refused','cd-ssh-connection-failed'),
    (b'Connection timed out','cd-ssh-connection-failed'),
    (b'No route to host','cd-ssh-connection-failed'),
    (b'Could not resolve hostname','cd-ssh-connection-failed'),
    (b'unknown SSH failure','cd-ssh-transport-failed'),
])
def test_ssh_failure_is_classified_without_echoing_stderr(client, monkeypatch, capsys, stderr, reason):
    calls=[]
    def run(*args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=255,stdout=b'',stderr=stderr+b' PRIVATE-SENTINEL')
    monkeypatch.setattr(runner.subprocess,'run',run)
    with pytest.raises(DockerError, match=reason) as caught:
        client.call({'op':'manager-prepare','length':1},raw=b'x')
    runner.report_failure(caught.value)
    output=capsys.readouterr().out
    assert reason in output and 'PRIVATE-SENTINEL' not in output
    assert len(calls)==1


@pytest.mark.parametrize('document,returncode,reason', [
    ({'status':'blocked','reason':'cd-trusted-intake-failed'},2,'cd-remote-intake-blocked'),
    ({'status':'blocked','reason':'private host detail'},2,'cd-remote-command-failed'),
    ({'status':'recovery-required'},2,'cd-remote-recovery-required'),
    ({'status':'rolled-back'},2,'cd-remote-recovery-required'),
    ({'status':'blocked'},0,'cd-remote-command-failed'),
    ({'status':'prepared','candidate':{}},2,'cd-remote-command-failed'),
])
def test_remote_failure_cannot_look_like_success(client, monkeypatch, capsys, document, returncode, reason):
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=returncode,stdout=json.dumps(document).encode(),stderr=b'PRIVATE-SENTINEL'))
    with pytest.raises(DockerError,match=reason) as caught: client.call({'op':'manager-prepare'})
    runner.report_failure(caught.value)
    output=capsys.readouterr().out
    assert reason in output and 'PRIVATE-SENTINEL' not in output and 'private host detail' not in output


def test_valid_prepared_response_is_preserved(client,monkeypatch):
    document={'status':'prepared','candidate':{'unchanged':'exact candidate'}}
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout=json.dumps(document).encode(),stderr=b''))
    assert client.call({'op':'manager-prepare'})==document


@pytest.mark.parametrize('status', ['blocked', 'recovery-required'])
def test_reviewed_remote_diagnostic_survives_transport_without_private_text(client, monkeypatch, capsys, status):
    document = {'status': status, 'reason': 'cd-trusted-intake-failed',
                'diagnostic': 'cd-journal-recovery-required', 'private': 'PRIVATE-SENTINEL'}
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=2, stdout=json.dumps(document).encode(), stderr=b'PRIVATE-SENTINEL'))
    with pytest.raises(DockerError, match='cd-journal-recovery-required') as caught:
        client.call({'op': 'manager-prepare'})
    runner.report_failure(caught.value)
    output = capsys.readouterr().out
    assert 'unfinished journal' in output and 'do not replay' in output and 'PRIVATE-SENTINEL' not in output


@pytest.mark.parametrize('diagnostic', ['PRIVATE-SENTINEL', 'cd-journal-recovery-required PRIVATE-SENTINEL',
                                    [], {}, None, 123])
def test_unreviewed_or_malformed_remote_diagnostics_remain_redacted(client, monkeypatch, capsys, diagnostic):
    document = {'status': 'blocked', 'reason': 'cd-trusted-intake-failed', 'diagnostic': diagnostic}
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=2, stdout=json.dumps(document).encode(), stderr=b'PRIVATE-SENTINEL'))
    with pytest.raises(DockerError, match='cd-remote-intake-blocked') as caught:
        client.call({'op': 'manager-prepare'})
    runner.report_failure(caught.value)
    assert 'PRIVATE-SENTINEL' not in capsys.readouterr().out


def test_bridge_catches_journal_gate_without_losing_specific_safe_code(monkeypatch, capsys):
    import io
    from scripts.cd import bridge
    monkeypatch.setattr(bridge.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"op":"manager-prepare"}\n')))
    def fail(*args): raise DockerError('cd-journal-recovery-required')
    monkeypatch.setattr(bridge, 'handle', fail)
    assert bridge.main('automation') == 2
    document = json.loads(capsys.readouterr().out)
    assert document == {'status': 'blocked', 'reason': 'cd-trusted-intake-failed',
                        'diagnostic': 'cd-journal-recovery-required'}


@pytest.mark.parametrize('error', [RuntimeError('PRIVATE-SENTINEL'), DockerError('PRIVATE-SENTINEL')])
def test_bridge_unknown_failures_still_fail_closed_and_redacted(monkeypatch, capsys, error):
    import io
    from scripts.cd import bridge
    monkeypatch.setattr(bridge.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"op":"manager-prepare"}\n')))
    def fail(*args): raise error
    monkeypatch.setattr(bridge, 'handle', fail)
    assert bridge.main('automation') == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'blocked', 'reason': 'cd-trusted-intake-failed'}


@pytest.mark.parametrize('code', [
    'production-approval-stale-or-binding-invalid',
    'production-exact-candidate-changed',
    'production-active-config-changed',
    'production-release-budget-exhausted',
    'production-execution-dependency-unavailable',
])
def test_bootstrap_failure_code_crosses_helper_bridge_and_client_without_private_data(
        client, monkeypatch, capsys, code):
    import io
    from scripts.cd import bootstrap, bridge

    helper = {'status': 'blocked', 'reasons': [code], 'private': 'PRIVATE-SENTINEL'}
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=2, stdout=json.dumps(helper).encode(), stderr=b'PRIVATE-SENTINEL'))
    monkeypatch.setattr(bridge.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"op":"test"}\n')))
    monkeypatch.setattr(bridge, 'handle', lambda *a: bootstrap.checked_process(['/fixed/helper']))
    assert bridge.main('automation') == 2
    response = capsys.readouterr().out
    assert json.loads(response)['diagnostic'] == code
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=2, stdout=response.encode(), stderr=b'PRIVATE-SENTINEL'))
    with pytest.raises(DockerError, match=code) as caught:
        client.call({'op': 'bootstrap-execute'})
    runner.report_failure(caught.value)
    output = capsys.readouterr().out
    assert code in output and 'PRIVATE-SENTINEL' not in output


@pytest.mark.parametrize('raw,reason', [(b'', 'cd-ssh-result-unavailable'),
    (b'PRIVATE-SENTINEL invalid JSON','cd-ssh-result-invalid'),
    (b'[]','cd-ssh-result-invalid'),(b'x'*131073,'cd-ssh-result-unavailable')])
def test_invalid_response_is_redacted(client,monkeypatch,capsys,raw,reason):
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=raw,stderr=b''))
    with pytest.raises(DockerError,match=reason) as caught:client.call({'op':'manager-prepare'})
    runner.report_failure(caught.value)
    assert 'PRIVATE-SENTINEL' not in capsys.readouterr().out


def test_unknown_timeout_has_no_automatic_retry(client,monkeypatch,capsys):
    calls=[]
    def run(*a,**k):
        calls.append(a)
        raise subprocess.TimeoutExpired('PRIVATE-SENTINEL',10,output=b'PRIVATE-SENTINEL')
    monkeypatch.setattr(runner.subprocess,'run',run)
    with pytest.raises(DockerError,match='cd-ssh-timeout') as caught:client.call({'op':'manager-execute'})
    runner.report_failure(caught.value)
    output=capsys.readouterr().out
    assert 'remote outcome may be unknown' in output and 'PRIVATE-SENTINEL' not in output
    assert len(calls)==1


@pytest.mark.parametrize('policy_id',['','wrong','sha256:'+'A'*64,'sha256:'+'a'*63])
def test_missing_or_invalid_policy_fails_before_client_creation(monkeypatch,capsys,policy_id):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('GITHUB_EVENT_NAME','workflow_run')
    monkeypatch.setenv('CD_POLICY_ID',policy_id)
    monkeypatch.setattr(runner.sys,'argv',['runner','manager','unused'])
    monkeypatch.setattr(runner,'Client',lambda *a,**k:pytest.fail('must fail before SSH/key materialization'))
    assert runner.entrypoint()==1
    output=capsys.readouterr().out
    assert 'cd-policy-id-required' in output and 'operator-approved signed standing policy' in output


def test_valid_policy_id_passes_without_network(monkeypatch):
    monkeypatch.setenv('CD_POLICY_ID','sha256:'+'a'*64)
    runner.require_policy_id()


@pytest.mark.parametrize('action,scope,blocked', [
    ('install','first-bootstrap',False),('prepare','first-bootstrap',False),
    ('execute','first-bootstrap',False),('execute','prepare-project-router-image',False),
    ('prepare','first-project-router-rollout',False),('execute','first-project-router-rollout',True),
])
def test_initial_bootstrap_can_establish_policy_but_rollout_requires_it(monkeypatch,tmp_path,capsys,action,scope,blocked):
    for key,value in {'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch',
        'GITHUB_REPOSITORY':'DanilFaritovich/portfolio-deployment','GITHUB_REF':'refs/heads/main',
        'REF_PROTECTED':'true','BOOTSTRAP_MANAGER_SHA':'a'*40,'BOOTSTRAP_ACTION':action,'BOOTSTRAP_SCOPE':scope}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.delenv('CD_POLICY_ID',raising=False)
    monkeypatch.setattr(bootstrap_runner.sys,'argv',['bootstrap',str(tmp_path)])
    monkeypatch.setattr('scripts.caddy_producer.wait_for_offline',lambda *a,**k:None)
    clients=[]
    monkeypatch.setattr(bootstrap_runner,'Client',lambda *a,**k:(clients.append(k) or object()))
    monkeypatch.setattr(bootstrap_runner,'dispatch',lambda *a,**k:{'status':'succeeded'})
    assert bootstrap_runner.entrypoint()==(1 if blocked else 0)
    assert bool(clients) is not blocked
    if blocked:assert 'cd-policy-id-required' in capsys.readouterr().out


@pytest.mark.parametrize('error',[RuntimeError('PRIVATE-SENTINEL'),DockerError('PRIVATE-SENTINEL'),
    DockerError('cd-ssh-authentication-failed PRIVATE-SENTINEL')])
def test_unclassified_errors_never_echo_exception_text(error,capsys):
    runner.report_failure(error)
    assert 'PRIVATE-SENTINEL' not in capsys.readouterr().out


@pytest.mark.parametrize('port,expected', [(None,'22'),('','22'),('22','22'),('2222','2222'),('65535','65535')])
def test_explicit_ssh_port_preserves_strict_host_check(client,tmp_path,monkeypatch,port,expected):
    if port is None:monkeypatch.delenv('CD_PORT',raising=False)
    else:monkeypatch.setenv('CD_PORT',port)
    directory=tmp_path/'port-client'
    directory.mkdir()
    configured=runner.Client(directory)
    commands=[]
    def run(argv,**kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0,stdout=b'{"status":"prepared"}',stderr=b'')
    monkeypatch.setattr(runner.subprocess,'run',run)
    configured.call({'op':'manager-prepare'})
    argv=commands[0]
    assert argv[argv.index('-p')+1]==expected
    assert '-oStrictHostKeyChecking=yes' in argv
    assert '-oIdentityAgent=none' in argv and '-oIdentitiesOnly=yes' in argv
    assert argv[-2:]==['portfolio-cd@example.invalid','portfolio-cd-v1']


@pytest.mark.parametrize('port',['0','65536','-1','+2222','02222','2222 PRIVATE-SENTINEL','999999999999999999999',' 2222'])
def test_invalid_port_is_rejected_before_key_files(client,monkeypatch,capsys,port):
    monkeypatch.setenv('CD_PORT',port)
    with pytest.raises(DockerError,match='cd-ssh-port-invalid') as caught:
        runner.Client('/does-not-exist-or-materialize-keys')
    runner.report_failure(caught.value)
    assert 'PRIVATE-SENTINEL' not in capsys.readouterr().out


def test_both_workflows_forward_the_configured_ssh_port():
    from pathlib import Path
    import yaml
    root=Path(__file__).resolve().parents[1]
    for name,job in [('cd-deploy.yml','deploy'),('production-bootstrap.yml','bootstrap')]:
        workflow=yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
        step=next(s for s in workflow['jobs'][job]['steps'] if 'CD_HOST' in s.get('env',{}))
        assert step['env']['CD_PORT']=='${{ vars.CD_PORT }}'


@pytest.mark.parametrize('failed', [False, True])
def test_manager_entrypoint_uses_only_automation_without_observer(client,monkeypatch,capsys,failed):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('GITHUB_EVENT_NAME','workflow_run')
    monkeypatch.setenv('CD_POLICY_ID','sha256:'+'a'*64)
    monkeypatch.delenv('CD_OBSERVER_KEY_B64')
    monkeypatch.setattr(runner.sys,'argv',['runner','manager','unused'])
    monkeypatch.setattr(runner.threading,'Thread',lambda *a,**k:pytest.fail('Manager must not start observer intake'))
    calls=[]
    def deploy(configured,kind,directory):
        assert set(configured.keys)=={'automation'}
        assert kind=='manager' and directory=='unused'
        calls.append(kind)
        if failed:raise DockerError('cd-remote-intake-blocked')
        return {'status':'succeeded'}
    monkeypatch.setattr(runner,'deploy',deploy)
    assert runner.entrypoint()==(1 if failed else 0)
    assert calls==['manager']
    output=capsys.readouterr().out
    if failed:assert 'cd-remote-intake-blocked' in output
    else:assert json.loads(output)=={'status':'succeeded'}


@pytest.mark.parametrize('kind,failed', [('application',False),('application',True),('observe-initial',False)])
def test_public_operations_keep_observer_and_always_stop_worker(client,monkeypatch,capsys,kind,failed):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('GITHUB_EVENT_NAME','workflow_dispatch' if kind=='observe-initial' else 'workflow_run')
    monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    monkeypatch.setenv('CD_POLICY_ID','sha256:'+'a'*64)
    monkeypatch.setattr(runner.sys,'argv',['runner',kind,'unused'])
    if kind=='observe-initial':monkeypatch.delenv('CD_AUTOMATION_KEY_B64')
    events=[]
    class Worker:
        def __init__(self,*,target,args,daemon):
            configured=target.__self__
            assert target.__func__ is runner.Client.observer and daemon is True
            assert set(configured.keys)==({'observer'} if kind=='observe-initial' else {'automation','observer'})
            self.stop,self.observed=args
        def start(self):
            events.append('started')
            if kind=='observe-initial':self.observed.set()
        def join(self,*,timeout):
            assert self.stop.is_set() and timeout==30
            events.append('joined')
    monkeypatch.setattr(runner.threading,'Thread',Worker)
    calls=[]
    def deploy(configured,actual,directory):
        assert actual=='application' and directory=='unused'
        calls.append(actual)
        if failed:raise DockerError('cd-remote-intake-blocked')
        return {'status':'succeeded'}
    monkeypatch.setattr(runner,'deploy',deploy)
    assert runner.entrypoint()==(1 if failed else 0)
    assert events==['started','joined']
    assert calls==([] if kind=='observe-initial' else ['application'])
    output=capsys.readouterr().out
    if failed:assert 'cd-remote-intake-blocked' in output
    else:assert json.loads(output)=={'status':'observed' if kind=='observe-initial' else 'succeeded'}
