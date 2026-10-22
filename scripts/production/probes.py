"""Fixed read-only commands and strict, lossy normalization; no remote script upload."""

import ipaddress
import json
import re

from scripts.compose import LABEL
from scripts.production import InspectionError

DOCKER = 'env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/docker --config /nonexistent --host unix:///var/run/docker.sock '
# Never request full inspect, environment, mounts, log content or arbitrary labels.
LABELS = ','.join('"' + field + '":{{json (index .Labels "' + label + '")}}' for field, label in (
    ('managed', LABEL + '.managed'), ('application', LABEL + '.application'),
    ('component', LABEL + '.component'), ('project', 'com.docker.compose.project')))
COMMANDS = {
    'os': "awk -F= '$1 == \"ID\" || $1 == \"VERSION_ID\" {print}' /etc/os-release",
    'architecture': 'uname -m',
    'cpu': 'getconf _NPROCESSORS_ONLN',
    'memory': "awk '/^MemTotal:/ {print $2}' /proc/meminfo",
    'disk': 'df -Pk / /var /opt',
    'hostname': 'hostname',
    'machine_id': 'cat /etc/machine-id',
    'engine': DOCKER + "version --format '{{json .Server.Version}}'",
    'daemon': DOCKER + "info --format '{{json .ID}}'",
    'compose': DOCKER + 'compose version --short',
    'containers': DOCKER + "ps -a --no-trunc --format '{\"name\":{{json .Names}},\"id\":{{json .ID}},\"state\":{{json .State}},\"ports\":{{json .Ports}},"
                  + ','.join('"' + field + '":{{json (.Label "' + label + '")}}' for field, label in (
                      ('managed', LABEL + '.managed'), ('application', LABEL + '.application'),
                      ('component', LABEL + '.component'), ('project', 'com.docker.compose.project'))) + "}'",
    # Only validated hex IDs become parameters; pipe failures are covered by the
    # separate listing sentinel and normalizer. One failed inspect fails the probe.
    'networks': 'set -f; ids=$(' + DOCKER + 'network ls -q --no-trunc) || exit 1; '
                'printf "NETWORKS\\n"; count=0; for id in $ids; do '
                'case "$id" in *[!0-9a-f]*|\'\') exit 1;; esac; '
                '[ ${#id} -eq 64 ] || exit 1; count=$((count+1)); [ "$count" -le 128 ] || exit 1; '
                + DOCKER + 'network inspect --format \'{"name":{{json .Name}},"id":{{json .Id}},'
                '"driver":{{json .Driver}},"ipam":{{json .IPAM.Config}},' + LABELS + '}\' "$id" || exit 1; done',
    'volumes': DOCKER + "volume ls --format '{\"name\":{{json .Name}},\"driver\":{{json .Driver}},"
               + ','.join('"' + field + '":{{json (.Label "' + label + '")}}' for field, label in (
                   ('managed', LABEL + '.managed'), ('application', LABEL + '.application'),
                   ('component', LABEL + '.component'), ('project', 'com.docker.compose.project'))) + "}'",
    'listeners': 'ss -H -lnt',
    'caddy': '/usr/bin/caddy version',
    'services': 'systemctl show caddy.service nginx.service apache2.service docker.service --property=Id,LoadState,ActiveState --no-pager',
    'firewall': 'ufw status',
}
CATEGORIES = {name: ('docker' if name in ('engine', 'daemon', 'compose', 'containers', 'networks', 'volumes')
                     else 'network' if name in ('listeners', 'caddy', 'services', 'firewall') else 'system') for name in COMMANDS}


def token(value, maximum=256, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not value and not empty):
        raise ValueError
    if value and not re.fullmatch(r'[A-Za-z0-9_./:@+,=() *%\[\]<>-]+', value):
        raise ValueError
    return value


def normalize(name, raw):
    """A successful exit is insufficient: malformed/incomplete evidence stays unknown."""
    try:
        text = raw.decode('utf-8').strip()
        if name == 'os':
            values = dict(line.split('=', 1) for line in text.splitlines())
            if set(values) != {'ID', 'VERSION_ID'}:
                raise ValueError
            return {key.lower(): token(value.strip('"'), 32) for key, value in values.items()}
        if name == 'caddy':
            value = text.split()[0] if text else ''
            if not re.fullmatch(r'v2\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', value):
                raise ValueError
            return {'value': value}
        if name in ('architecture', 'hostname', 'machine_id', 'engine', 'daemon', 'compose'):
            value = json.loads(text) if name in ('engine', 'daemon') else text
            value = token(value)
            if name == 'machine_id' and not re.fullmatch(r'[0-9a-f]{32}', value):
                raise ValueError
            if name == 'engine' and not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', value):
                raise ValueError
            if name == 'daemon' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:-]{7,127}', value):
                raise ValueError
            if name == 'compose' and not re.fullmatch(r'v?2\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', value):
                raise ValueError
            return {'value': value}
        if name in ('cpu', 'memory'):
            if not text.isdecimal() or not 0 < int(text) < 2**50:
                raise ValueError
            return {'value': int(text)}
        if name == 'disk':
            lines = text.splitlines()
            if len(lines) < 2 or len(lines) > 16:
                raise ValueError
            records = []
            for line in lines[1:]:
                fields = line.split()
                if len(fields) != 6 or not all(item.isdecimal() for item in fields[1:4]):
                    raise ValueError
                records.append({'mount': token(fields[5]), 'availableKiB': int(fields[3])})
            if '/' not in {r['mount'] for r in records}:
                raise ValueError
            return {'filesystems': records}
        if name in ('containers', 'networks', 'volumes'):
            lines = text.splitlines()
            if name == 'networks':
                if not lines or lines.pop(0) != 'NETWORKS':
                    raise ValueError
            if len(lines) > 128:
                raise ValueError
            records = []
            fields = {'managed', 'application', 'component', 'project', 'name'} | (
                {'id', 'state', 'ports'} if name == 'containers' else
                {'id', 'driver', 'ipam'} if name == 'networks' else {'driver'})
            for line in lines:
                item = json.loads(line)
                if not isinstance(item, dict) or set(item) != fields:
                    raise ValueError
                record = {key: token('' if value is None and key in ('managed', 'application', 'component', 'project') else value, empty=True)
                          for key, value in item.items() if key != 'ipam'}
                if not record['name']:
                    raise ValueError
                if 'id' in record and not re.fullmatch(r'[0-9a-f]{64}', record['id']):
                    raise ValueError
                if name == 'networks':
                    ipam = item['ipam']
                    if ipam is not None and (not isinstance(ipam, list) or len(ipam) > 16):
                        raise ValueError
                    record['subnets'] = [str(ipaddress.ip_network(config['Subnet'], strict=True)) for config in ipam or []]
                record['ownership'] = ('claimed-managed' if record['managed'] == 'true' and
                                       record['application'] and record['project'] == 'portfolio-' + record['application']
                                       else 'foreign-or-unknown')
                records.append(record)
            return {'resources': records}
        if name == 'listeners':
            ports = set()
            for line in text.splitlines():
                fields = line.split()
                if len(fields) < 5 or fields[0] != 'LISTEN':
                    raise ValueError
                port = fields[3].rsplit(':', 1)[-1]
                if not port.isdecimal() or not 1 <= int(port) <= 65535:
                    raise ValueError
                ports.add(int(port))
            return {'ports': sorted(ports)}
        if name == 'services':
            services = []
            for block in text.split('\n\n'):
                values = dict(line.split('=', 1) for line in block.splitlines())
                if set(values) != {'Id', 'LoadState', 'ActiveState'}:
                    raise ValueError
                services.append({key: token(value) for key, value in values.items()})
            if {s['Id'] for s in services} != {'caddy.service', 'nginx.service', 'apache2.service', 'docker.service'}:
                raise ValueError
            return {'services': services}
        if name == 'firewall':
            if text == 'Status: inactive':
                return {'state': 'inactive'}
            if text.startswith('Status: active\n'):
                return {'state': 'active', 'rules': 'not-collected'}
            raise ValueError
        raise ValueError
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise InspectionError('invalid-or-incomplete-probe-response') from None
