"""Shared HostPolicy/bootstrap IPAM rules; synthetic authenticated samples only."""
from dataclasses import replace

import pytest

from scripts.docker_adapter import DockerError
from scripts.docker_discovery import DockerObservation, DockerResource
from scripts.production_integration.host_policy import validate_network_inventory
from scripts.reconciliation import encode
from test_production_preparation import bootstrap, bootstrap_sample


def observation(name='foreign', driver='bridge', scope='local', ipam=None):
    return DockerObservation((DockerResource('network', 'a'*64, name, encode({
        'driver': driver, 'scope': scope, 'ipam': ipam})),), (), True)


def proposal(docker):
    return bootstrap(replace(bootstrap_sample(), docker=docker))


@pytest.mark.parametrize('name,driver,scope', [
    ('host', 'null', 'local'), ('none', 'host', 'local'),
    ('host', 'bridge', 'local'), ('none', 'bridge', 'local'),
    ('foreign', 'host', 'local'), ('foreign', 'null', 'local'),
    ('host', 'host', 'swarm'), ('none', 'null', None),
    ('foreign', 'overlay', 'local'), ('foreign', 'bridge', 'global'),
])
def test_unconfirmed_network_identity_blocks_both_consumers(name, driver, scope):
    docker = observation(name, driver, scope, {'Driver': 'default', 'Config': None})
    with pytest.raises(DockerError, match='production-host-network-ipam-unknown'):
        validate_network_inventory(docker)
    plan = proposal(docker)
    assert plan.blockers == ('bootstrap-network-ipam-unverified',)
    assert not plan.operations


@pytest.mark.parametrize('ipam', [None, {}, [],
    {'Driver': 'default', 'Config': None}, {'Driver': 'default', 'Config': []},
    {'Driver': 'default', 'Config': {}}, {'Driver': 'default', 'Config': 'invalid'},
    {'Driver': 'foreign', 'Config': [{'Subnet': '10.0.0.0/24'}]},
    *[{'Driver': 'default', 'Config': [config]} for config in [
        None, [], 'invalid', {}, {'Subnet': None}, {'Subnet': 123},
        {'Subnet': 'invalid'}, {'Subnet': '10.0.0.1/24'},
        {'Subnet': '10.0.0.0/24', 'Gateway': '10.1.0.1'},
        {'Subnet': '10.0.0.0/24', 'Gateway': []},
    ]],
])
def test_foreign_network_missing_or_malformed_ipam_fails_closed(ipam):
    docker = observation(ipam=ipam)
    with pytest.raises(DockerError, match='production-host-network-ipam-unknown'):
        validate_network_inventory(docker)
    assert proposal(docker).blockers == ('bootstrap-network-ipam-unverified',)
    assert not proposal(docker).operations


@pytest.mark.parametrize('subnet,collision', [
    ('10.0.0.0/24', False), ('2001:db8::/64', False),
    ('172.30.0.0/24', True), ('172.31.0.0/24', True), ('172.30.0.0/15', True),
])
def test_all_foreign_subnets_are_compared_with_both_managed_networks(subnet, collision):
    docker = observation(ipam={'Driver': 'default', 'Config': [
        {'Subnet': '192.168.0.0/24'}, {'Subnet': subnet}]})
    validate_network_inventory(docker)
    plan = proposal(docker)
    if collision:
        assert plan.blockers == ('bootstrap-ipam-collision',)
        assert not plan.operations
    else:
        assert plan.blockers == ('stage-09-4b-bootstrap-approval-and-execution-required',)
        assert plan.operations


def test_valid_defaults_do_not_hide_an_unverified_foreign_network():
    docker = observation('host', 'host')
    docker = replace(docker, resources=docker.resources + observation().resources)
    with pytest.raises(DockerError): validate_network_inventory(docker)
    assert proposal(docker).blockers == ('bootstrap-network-ipam-unverified',)
