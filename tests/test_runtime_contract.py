"""Runtime policy regression boundaries; no Engine access in the offline suite."""
from dataclasses import replace
import json

import pytest

from scripts.docker_discovery import container_runtime, container_runtime_verification
from scripts.reconciliation import encode
from scripts.runtime_security import matches, differing_fields
from test_production_integration import router, registration, state  # shared synthetic contract


def verify(router, mutate=None):
    artifact, resource, image = state(router)
    facts = resource.facts
    if mutate:
        mutate(facts)
    resource = replace(resource, facts_json=encode(facts))
    result = container_runtime_verification(resource, artifact, 'web', image)
    assert container_runtime(resource, artifact, 'web', image) is result.matched
    return result


def test_legacy_tmpfs_is_not_a_mountpoint(router):
    assert verify(router, lambda f: f.update(mounts=[])).matched


@pytest.mark.parametrize('key,value,group,field', [
    ('imageId', 'wrong-id', 'image', 'ImageID'),
    ('config', None, 'input', 'Structure'),
    ('networks', {}, 'networks', 'Networks'),
    ('mounts', [{'Type': 'bind', 'Destination': '/socket'}], 'mounts', 'Mounts'),
    ('mounts', [{'Type': 'tmpfs', 'Destination': '/tmp', 'RW': False}], 'mounts', 'Mounts'),
    ('mounts', [{'Type': 'tmpfs', 'Destination': '/other', 'RW': True}], 'mounts', 'Mounts'),
    ('mounts', [{'Type': 'tmpfs', 'Destination': '/tmp', 'RW': True}] * 2, 'mounts', 'Mounts'),
])
def test_structured_groups(router, key, value, group, field):
    result = verify(router, lambda f: f.update({key: value}))
    assert (group, field) in {(m.group, m.field) for m in result.mismatches}


@pytest.mark.parametrize('field,value', [
    ('Privileged', True), ('Privileged', 'false'), ('CapAdd', ['SYS_ADMIN']),
    ('DeviceCgroupRules', ['a *:* rwm']), ('UTSMode', 'host'), ('UsernsMode', 'host'),
    ('CgroupnsMode', 'host'), ('CapDrop', ['ALL', 'private-value']), ('CapDrop', 'ALL'),
    ('ReadonlyRootfs', 1), ('SecurityOpt', ['no-new-privileges', 'seccomp=unconfined']),
    ('SecurityOpt', 'private-value'), ('RestartPolicy', {'Name': 'unless-stopped', 'MaximumRetryCount': True}),
    ('Tmpfs', {'/tmp': 'rw,rw,noexec,nosuid,nodev,size=16777216,mode=1777'}),
    ('Tmpfs', {'/tmp': 'rw,noexec,nosuid,nodev,size=0,mode=1777'}),
    ('Tmpfs', []), ('PidsLimit', False), ('Runtime', 'untrusted'), ('IpcMode', 'shareable'),
    ('Mounts', [{'Type': 'bind', 'Source': '/private-value', 'Target': '/tmp'}]),
])
def test_host_changes_reject_without_sensitive_diagnostics(router, field, value):
    result = verify(router, lambda f: f['host'].update({field: value}))
    assert not result.matched
    raw = encode(result.diagnostic())
    assert 'private-value' not in raw and 'SYS_ADMIN' not in raw and 'seccomp=' not in raw
    assert field in raw or field == 'Mounts' and 'MountRequests' in raw


@pytest.mark.parametrize('field,value', [('Cmd', 'serve'), ('Entrypoint', ['/private-value']),
    ('Env', ['SECRET=private-value']), ('User', 101), ('WorkingDir', '/private-value'),
    ('Healthcheck', {'Test': ['CMD', '/check'], 'Interval': False}),
    ('Healthcheck', {'Test': ['CMD', '/check'], 'private-value': 10}),
    ('Volumes', {'/private-value': {}}), ('ExposedPorts', {'123/tcp': {}}), ('Tty', True)])
def test_config_types_and_overrides(router, field, value):
    result = verify(router, lambda f: f['config'].update({field: value}))
    assert not result.matched
    assert 'private-value' not in encode(result.diagnostic())
    assert 'SECRET' not in encode(result.diagnostic())
    assert any(m.group == 'config' and m.field == field for m in result.mismatches)


def test_documented_zero_and_set_equivalences(router):
    artifact, resource, image = state(router)
    facts = resource.facts
    facts['host']['NetworkMode'] = artifact.model['networks']['proxy']['name']
    facts['host']['Runtime'] = 'runc'
    facts['host']['PidsLimit'] = 0
    facts['host']['IpcMode'] = 'private'
    facts['host']['SecurityOpt'] = ['no-new-privileges:true']
    facts['host']['Tmpfs']['/tmp'] = ','.join(reversed(facts['host']['Tmpfs']['/tmp'].split(',')))
    facts['config']['Healthcheck']['Interval'] = 0
    facts['config']['WorkingDir'] = ''
    facts['config']['Cmd'] = []
    facts['config']['Volumes'] = {}
    for network in facts['networks'].values():
        network['Aliases'] += [resource.name, resource.identifier[:12]]
    result = container_runtime_verification(replace(resource, facts_json=encode(facts)), artifact, 'web', image)
    assert result.matched, result.diagnostic()
    assert matches(facts['host'], artifact.model['services']['web'])
    assert differing_fields(facts['host'], artifact.model['services']['web']) == ()


@pytest.mark.parametrize('mode', ['host', 'none', 'bridge', 'container:foreign', 'foreign-network'])
def test_network_mode_never_expands_declared_scope(router, mode):
    result = verify(router, lambda f: f['host'].update(NetworkMode=mode))
    assert any(m.group == 'networks' and m.field == 'NetworkMode' for m in result.mismatches)


def test_untrusted_alias_not_reported(router):
    def change(f):
        next(iter(f['networks'].values()))['Aliases'].append('private-value')
    result = verify(router, change)
    assert not result.matched
    assert 'private-value' not in json.dumps(result.diagnostic())


@pytest.mark.parametrize('ipam,valid', [(None, True), ({}, True),
    ({'IPv4Address': '', 'IPv6Address': '', 'LinkLocalIPs': None}, True),
    ({'IPv4Address': '192.0.2.1'}, False), ({'LinkLocalIPs': ['169.254.1.1']}, False),
    ({'private-value': ''}, False), ({'IPv4Address': 0}, False)])
def test_endpoint_ipam_zero_defaults_only(router, ipam, valid):
    def change(f):
        for network in f['networks'].values(): network['IPAMConfig'] = ipam
    result = verify(router, change)
    assert result.matched is valid
    assert 'private-value' not in encode(result.diagnostic())


def test_integration_workflow_is_separate_disposable_pr_job():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/runtime-integration.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'pull_request'}
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['runtime-integration']
    assert job['runs-on'] == 'ubuntu-24.04'
    text = encode(workflow)
    assert 'persist-credentials' in text and 'runtime-integration-test' in text
    assert 'secrets.' not in text and 'workflow_dispatch' not in text
    assert 'application_acceptance' not in text and 'pull_request_target' not in text


def test_integration_make_target_requires_explicit_opt_in(monkeypatch, capsys):
    from scripts.runtime_integration import main
    with pytest.raises(SystemExit):
        main(['--endpoint', 'tcp://production:2375', '--authorize', 'disposable-runtime-integration'])
    with pytest.raises(SystemExit):
        main(['--endpoint', 'unix:///var/run/docker.sock', '--authorize', ''])


def test_integration_error_always_attempts_cleanup(tmp_path, monkeypatch):
    from scripts import runtime_integration as integration
    events = []
    def failed(self):
        events.append('started')
        raise integration.IntegrationFailure('injected-failure')
    monkeypatch.setattr(integration.Harness, 'run', failed)
    monkeypatch.setattr(integration.Harness, 'cleanup', lambda self: events.append('cleanup'))
    assert integration.main(['--endpoint', 'unix:///var/run/docker.sock', '--authorize', 'disposable-runtime-integration']) == 1
    assert events == ['started', 'cleanup']


@pytest.mark.parametrize('field,value', [('Privileged', 0), ('Privileged', []),
    ('PortBindings', []), ('CapAdd', {}), ('CpuQuota', False), ('Init', 0), ('Mounts', {})])
def test_wrong_zero_type_is_not_an_engine_default(router, field, value):
    assert not verify(router, lambda f: f['host'].update({field: value})).matched
