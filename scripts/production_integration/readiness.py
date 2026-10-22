"""Bounded production readiness adapter over approved read-only transport ports.

No HTTP/SSH client is constructed here. An operator-approved internal HTTP port
must implement get(ip,port,path,timeout,follow_redirects=False) -> status. Runtime
secret delivery is unsupported. Docker identity is verified by the Engine port.
"""
import ipaddress
import re

from scripts.compose import LABEL
from scripts.docker_adapter import DockerError
from scripts.readiness import ProbeResult


class ProductionProbes:
    def __init__(self, docker, http, application, private_subnet):
        self.docker, self.http, self.application = docker, http, application
        self.subnet = ipaddress.ip_network(private_subnet, strict=True)
        if self.subnet.version != 4:
            raise DockerError('production-probe-network-invalid')

    def probe(self, component, container_id, *, timeout, follow_redirects):
        if (follow_redirects is not False or type(timeout) not in (int, float) or not 0 < timeout <= 10 or
            not re.fullmatch(r'[0-9a-f]{64}', container_id)):
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
            try:
                address = ipaddress.ip_address(private.get('IPAddress', ''))
                if address not in self.subnet or address in (self.subnet.network_address, self.subnet.broadcast_address):
                    raise ValueError
            except ValueError:
                raise DockerError('production-probe-endpoint-unverified') from None
            # Port/path come exclusively from admitted runtime. No public DNS,
            # redirects, host headers or HTTPS trust bypass is involved.
            status = self.http.get(str(address), component['port'], component['health']['path'],
                                   timeout=timeout, follow_redirects=False)
            if type(status) is not int or not 100 <= status <= 599:
                raise DockerError('production-probe-result-invalid')
            result = ProbeResult(True, http_status=status)
        after = self.docker.inspect('container', container_id)
        keys = ('Id', 'Image', 'Config', 'HostConfig', 'NetworkSettings')
        if any(before.get(k) != after.get(k) for k in keys) or after.get('State', {}).get('Status') != 'running':
            raise DockerError('production-probe-runtime-changed')
        return result
