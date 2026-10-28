"""Bounded production readiness adapter over approved read-only transport ports.

No HTTP/SSH client is constructed here. An operator-approved internal HTTP port
must implement get(ip,port,path,timeout,follow_redirects=False) -> status. Runtime
secret delivery is unsupported. Docker identity is verified by the Engine port.
"""
import ipaddress
import math
import re

from scripts.compose import LABEL
from scripts.docker_adapter import DockerError
from scripts.readiness import ProbeResult


class ProductionProbes:
    def __init__(self, docker, http, application, private_subnet, *, network_id=None):
        self.docker, self.http, self.application = docker, http, application
        self.subnet = ipaddress.ip_network(private_subnet, strict=True)
        self.network_id = network_id
        if self.subnet.version != 4:
            raise DockerError('production-probe-network-invalid')

    def probe(self, component, container_id, *, timeout, follow_redirects):
        if (follow_redirects is not False or type(timeout) not in (int, float) or not 0 < timeout <= 10 or
            not math.isfinite(timeout) or not re.fullmatch(r'[0-9a-f]{64}', container_id)):
            raise DockerError('production-probe-bounds-invalid')
        before = self.docker.inspect('container', container_id)
        project = 'portfolio-' + self.application
        labels = (before.get('Config') or {}).get('Labels') or {}
        expected = {LABEL + '.managed': 'true', LABEL + '.application': self.application,
                    LABEL + '.component': component['name'], 'com.docker.compose.project': project,
                    'com.docker.compose.service': component['name']}
        if (before.get('Id') != container_id or any(labels.get(k) != v for k, v in expected.items()) or
            before.get('HostConfig', {}).get('PortBindings')):
            raise DockerError('production-probe-ownership-mismatch')
        state = before.get('State') or {}
        if state.get('Status') != 'running':
            return ProbeResult(False)
        if component['type'] == 'worker':
            result = ProbeResult(True, health=state.get('Health', {}).get('Status'))
        else:
            private = before.get('NetworkSettings', {}).get('Networks', {}).get(project + '-private') or {}
            network = self._network(project, private, container_id)
            try:
                address = ipaddress.ip_address(private.get('IPAddress', ''))
                if address not in self.subnet or address in (self.subnet.network_address, self.subnet.broadcast_address):
                    raise ValueError
            except ValueError:
                raise DockerError('production-probe-endpoint-unverified') from None
            # Port/path come exclusively from admitted runtime. No public DNS,
            # redirects, host headers or HTTPS trust bypass is involved.
            try:
                status = self.http.get(str(address), component['port'], component['health']['path'],
                                       timeout=timeout, follow_redirects=False)
            except DockerError as error:
                if error.code != 'production-http-read-unavailable':
                    raise
                # A starting listener supplies no HTTP evidence. Still close
                # the network/container checks before returning unknown health.
                status = None
            else:
                if type(status) is not int or not 100 <= status <= 599:
                    raise DockerError('production-probe-result-invalid')
            result = ProbeResult(True, http_status=status)
            if self._network(project, private, container_id) != network:
                raise DockerError('production-probe-network-changed')
        after = self.docker.inspect('container', container_id)
        keys = ('Id', 'Image', 'Config', 'HostConfig', 'NetworkSettings')
        if any(before.get(k) != after.get(k) for k in keys) or after.get('State', {}).get('Status') != 'running':
            raise DockerError('production-probe-runtime-changed')
        return result

    def _network(self, project, attachment, container_id):
        # A subnet match alone is not evidence of an admitted Docker network.
        if not isinstance(self.network_id, str) or not re.fullmatch(r'[0-9a-f]{64}', self.network_id):
            raise DockerError('production-probe-network-pin-required')
        network = self.docker.inspect('network', self.network_id)
        owner = network.get('Labels') or {}
        member = (network.get('Containers') or {}).get(container_id) or {}
        if (network.get('Id') != self.network_id or network.get('Name') != project + '-private' or
            attachment.get('NetworkID') != self.network_id or
            network.get('Driver') != 'bridge' or network.get('Scope') != 'local' or
            network.get('Internal') is not True or network.get('EnableIPv6') is not False or
            network.get('Options') != {} or
            any(owner.get(k) != v for k, v in {
                LABEL + '.managed': 'true', LABEL + '.application': self.application,
                'com.docker.compose.project': project, 'com.docker.compose.network': 'private'}.items()) or
            network.get('IPAM', {}).get('Driver') != 'default' or
            network.get('IPAM', {}).get('Options') not in ({}, None) or
            len(network.get('IPAM', {}).get('Config', [])) != 1 or
            network['IPAM']['Config'][0].get('Subnet') != str(self.subnet) or
            member.get('IPv4Address') != str(attachment.get('IPAddress')) + '/' + str(self.subnet.prefixlen)):
            raise DockerError('production-probe-network-unverified')
        return network
