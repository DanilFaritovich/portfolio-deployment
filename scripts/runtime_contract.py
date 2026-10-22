"""One effective-runtime policy for discovery and isolated acceptance.

OCI/source authentication and ownership are upstream prerequisites, not inferred
from container labels. Diagnostics contain only code-owned field names.
"""
from dataclasses import dataclass, asdict
import json

from scripts.runtime_security import differing_fields


@dataclass(frozen=True)
class RuntimeMismatch:
    group: str
    field: str
    code: str = 'runtime-field-mismatch'


@dataclass(frozen=True)
class RuntimeVerification:
    mismatches: tuple[RuntimeMismatch, ...]

    @property
    def matched(self):
        return not self.mismatches

    def diagnostic(self):
        return {'matched': self.matched, 'mismatches': [asdict(m) for m in self.mismatches]}


def _array(value):
    if value is None:
        return []
    if type(value) is not list or any(type(v) is not str for v in value):
        raise ValueError()
    return value


def _object(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise ValueError()
    return value


def _config_value(key, value):
    if key in ('Cmd', 'Entrypoint', 'Env'):
        return _array(value)
    if key in ('ExposedPorts', 'Volumes'):
        result = _object(value)
        if any(type(v) is not dict or v for v in result.values()):
            raise ValueError()
        return result
    if key == 'Healthcheck':
        result = dict(_object(value))
        if 'Test' in result:
            result['Test'] = _array(result['Test'])
        for name in ('Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries'):
            if name in result:
                if type(result[name]) is not int or result[name] < 0:
                    raise ValueError()
                # Go omitempty and omitted optional zero are the same setting.
                if result[name] == 0:
                    del result[name]
        if result.keys() - {'Test', 'Interval', 'Timeout', 'StartPeriod', 'StartInterval', 'Retries'}:
            raise ValueError()
        return result
    if value is None:
        return ''
    if type(value) is not str:
        raise ValueError()
    return value


def verify_runtime(resource, artifact, service, image):
    """Compare independent groups; malformed observations always fail closed.

    State/readiness is deliberately separate: stopped containers can lose network
    endpoints without having a different configuration. Callers classify state
    before asking for runtime/readiness on a live container.
    """
    failures = []

    def check(group, field, predicate):
        try:
            good = predicate()
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            failures.append(RuntimeMismatch(group, field, 'runtime-field-invalid'))
            return
        if not good:
            failures.append(RuntimeMismatch(group, field))

    try:
        facts, model = resource.facts, artifact.model
        wanted = model['services'][service]
        config, host = facts['config'], facts['host']
        defaults = json.loads(image.defaults_json)
        if any(type(x) is not dict for x in (facts, wanted, config, host, defaults)):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        return RuntimeVerification((RuntimeMismatch('input', 'Structure', 'runtime-field-invalid'),))

    check('image', 'Verified', lambda: image.verified is True)
    check('image', 'ImageID', lambda: facts['imageId'] == image.image_id)
    check('image', 'Image', lambda: config['Image'] == wanted['image'] == image.reference)
    for key in ('Cmd', 'Entrypoint', 'Env', 'User', 'WorkingDir', 'Healthcheck', 'ExposedPorts', 'Volumes', 'StopSignal'):
        check('config', key, lambda k=key: _config_value(k, config.get(k)) == _config_value(
            k, wanted.get('user', defaults.get(k)) if k == 'User' else defaults.get(k)))
    for key in ('Tty', 'OpenStdin', 'StdinOnce', 'NetworkDisabled'):
        check('config', key, lambda k=key: host_zero(config.get(k), False))

    forbidden = {
        'Privileged': bool, 'PortBindings': dict, 'PublishAllPorts': bool,
        'Binds': list, 'CapAdd': list, 'Devices': list, 'DeviceRequests': list,
        'DeviceCgroupRules': list, 'PidMode': str, 'UTSMode': str, 'VolumesFrom': list,
        'ExtraHosts': list, 'Links': list, 'UsernsMode': str, 'Sysctls': dict,
        'CgroupParent': str, 'VolumeDriver': str, 'ContainerIDFile': str, 'StorageOpt': dict,
    }
    for key, kind in forbidden.items():
        check('security', key, lambda k=key, t=kind: empty_field(host.get(k), t))
    check('security', 'IpcMode', lambda: host.get('IpcMode') in (None, '', 'private'))
    check('security', 'CgroupnsMode', lambda: host.get('CgroupnsMode') in (None, '', 'private'))
    # Each security field is checked once, by the same helper as the legacy API.
    try:
        for key in differing_fields(host, wanted):
            failures.append(RuntimeMismatch('mounts' if key == 'Tmpfs' else 'security', key))
    except (TypeError, ValueError, AttributeError):
        failures.append(RuntimeMismatch('security', 'Structure', 'runtime-field-invalid'))

    networks = {model['networks'][n]['name']: options for n, options in wanted['networks'].items()}
    # Compose selects a primary endpoint among declared networks. The complete
    # endpoint set below still has to match; no host/none/container/bridge mode.
    check('networks', 'NetworkMode', lambda: host.get('NetworkMode') in (None, '', *networks))
    check('networks', 'Networks', lambda: set(facts['networks']) == set(networks))
    for name, options in networks.items():
        def aliases_match():
            aliases = set(_array(facts['networks'][name].get('Aliases')))
            required = set(options.get('aliases', []))
            allowed = required | {service, resource.name, resource.identifier[:12]}
            return required <= aliases <= allowed
        check('networks', 'Aliases', aliases_match)
        for key, kind in (('Links', list), ('DriverOpts', dict)):
            check('networks', key, lambda k=key, t=kind, n=name: empty_field(facts['networks'][n].get(k), t))

        def ipam_match(n=name):
            ipam = _object(facts['networks'][n].get('IPAMConfig'))
            return (not ipam.keys() - {'IPv4Address', 'IPv6Address', 'LinkLocalIPs'} and
                    all(ipam.get(k) in (None, '') for k in ('IPv4Address', 'IPv6Address')) and
                    not _array(ipam.get('LinkLocalIPs')))
        check('networks', 'IPAMConfig', ipam_match)

    def mounts_match():
        mounts = facts['mounts']
        if type(mounts) is not list or any(type(m) is not dict for m in mounts):
            return False
        expected = {(model['volumes'][m['source']]['name'], m['target'], True) for m in wanted.get('volumes', [])}
        actual = set()
        destinations = set()
        for mount in mounts:
            target = mount.get('Destination')
            if target in destinations:
                return False
            destinations.add(target)
            if mount.get('Type') == 'volume':
                if mount.get('Driver', 'local') != 'local' or type(mount.get('RW')) is not bool:
                    return False
                actual.add((mount.get('Name'), target, mount['RW']))
            elif mount.get('Type') == 'tmpfs':
                # Legacy HostConfig.Tmpfs may be absent from inspect.Mounts.
                # Any metadata that IS present must corroborate the contract.
                if target not in (host.get('Tmpfs') or {}) or mount.get('RW') is not True:
                    return False
            else:
                return False
        return actual == expected
    check('mounts', 'Mounts', mounts_match)

    def mount_requests_match():
        # Compose long syntax named volumes use HostConfig.Mounts. tmpfs uses
        # HostConfig.Tmpfs in our renderer; alternate tmpfs/bind requests fail.
        requests = host.get('Mounts')
        requests = [] if requests is None else requests
        if type(requests) is not list:
            return False
        expected = {(model['volumes'][m['source']]['name'], m['target']) for m in wanted.get('volumes', [])}
        seen = set()
        for mount in requests:
            if mount.get('Type') != 'volume' or mount.get('ReadOnly', False) is not False:
                return False
            pair = (mount.get('Source'), mount.get('Target'))
            if pair not in expected or pair in seen:
                return False
            seen.add(pair)
            opts = _object(mount.get('VolumeOptions'))
            if opts.get('NoCopy') is not True or opts.keys() - {'NoCopy'}:
                return False
        return not requests or seen == expected
    check('mounts', 'MountRequests', mount_requests_match)

    engine_zero = {
        'CpuShares': int, 'CpuQuota': int, 'CpuPeriod': int, 'NanoCpus': int,
        'CpusetCpus': str, 'CpusetMems': str, 'Memory': int,
        'MemoryReservation': int, 'MemorySwap': int, 'Ulimits': list,
        'Dns': list, 'DnsSearch': list, 'DnsOptions': list, 'GroupAdd': list,
        'Init': bool, 'AutoRemove': bool,
    }
    for key, kind in engine_zero.items():
        check('engine', key, lambda k=key, t=kind: empty_field(host.get(k), t))
    check('engine', 'Runtime', lambda: host.get('Runtime') in (None, '', 'runc'))
    check('engine', 'PidsLimit', lambda: host.get('PidsLimit') is None or
          type(host['PidsLimit']) is int and host['PidsLimit'] == 0)
    return RuntimeVerification(tuple(dict.fromkeys(failures)))


def empty_field(value, kind):
    """Only the specific Engine field's typed Go zero or optional null."""
    return value is None or type(value) is kind and value == kind()


def host_zero(value, default):
    return value is None or type(value) is type(default) and value == default
