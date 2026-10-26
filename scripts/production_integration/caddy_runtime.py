"""Read-only Caddy Engine comparison. No transport, controller or write authority."""
import json

from scripts.docker_discovery import container_image_id_matches
from scripts.production_integration.caddy_image import inspect_caddy_contract
from scripts.runtime_contract import _config_value, empty_field
from scripts.runtime_security import differing_fields


def verify_caddy_runtime(runtime, artifact, evidence, doc):
    """Fixed diagnostics only; accepts production or isolated render projection.

    The caller owns attestation of the artifact/host. Passing this comparison
    alone never supplies ManagedGatewayReadPort's authenticated attestor.
    """
    inspect_caddy_contract(runtime, evidence)
    try:
        wanted = artifact.model['services']['caddy']
        baseline = runtime.render().model['services']['caddy']
        invariant_keys = ('image', 'platform', 'restart', 'read_only', 'cap_drop', 'cap_add', 'security_opt', 'command')
        if (any(wanted.get(k) != baseline[k] for k in invariant_keys) or
            wanted.get('ports') not in ([], baseline['ports']) or
            {(m['target'], m.get('read_only', False)) for m in wanted['volumes']} !=
                {(m['target'], m.get('read_only', False)) for m in baseline['volumes']} or
            len(wanted['volumes']) != len(baseline['volumes']) or
            any(wanted.get('labels', {}).get(k) != v for k, v in baseline['labels'].items())):
            return ('caddy-runtime-expectation-invalid',)
        config, host = doc['Config'], doc['HostConfig']
        defaults = json.loads(evidence.defaults_json)
        codes = []
        def check(ok, code):
            if not ok:
                codes.append(code)
        check(container_image_id_matches(evidence, doc['Image'], doc.get('ImageManifestDescriptor')) and
              config['Image'] == wanted['image'] == evidence.reference,
              'caddy-runtime-image-mismatch')
        check(all(_config_value(k, config.get(k)) == _config_value(k, defaults.get(k)) for k in
                  ('Cmd', 'Entrypoint', 'Env', 'User', 'WorkingDir', 'Healthcheck', 'ExposedPorts', 'Volumes', 'StopSignal')),
              'caddy-runtime-defaults-mismatch')
        check(not differing_fields(host, wanted), 'caddy-runtime-security-mismatch')
        check(host.get('CapAdd') in (['NET_BIND_SERVICE'], ['CAP_NET_BIND_SERVICE']), 'caddy-runtime-capabilities-mismatch')
        zero = {'Privileged': bool, 'PublishAllPorts': bool, 'Binds': list, 'Devices': list,
                'DeviceRequests': list, 'DeviceCgroupRules': list, 'PidMode': str, 'UTSMode': str,
                'UsernsMode': str, 'VolumesFrom': list, 'ExtraHosts': list, 'Links': list,
                'Sysctls': dict, 'GroupAdd': list, 'Dns': list, 'DnsSearch': list, 'DnsOptions': list,
                'StorageOpt': dict, 'CgroupParent': str}
        check(all(empty_field(host.get(k), t) for k, t in zero.items()) and
              host.get('IpcMode') in ('private', '', None) and host.get('CgroupnsMode') in ('private', '', None) and
              host.get('Runtime') in ('runc', '', None), 'caddy-runtime-isolation-mismatch')
        ports = wanted.get('ports', [])
        expected_ports = {f'{p.split(":")[1]}': [{'HostIp': '', 'HostPort': p.split(':')[0]}] for p in ports}
        check((host.get('PortBindings') or {}) == expected_ports, 'caddy-runtime-ports-mismatch')
        expected_mounts = {(m['source'], m['target'], not m.get('read_only', False)) for m in wanted['volumes']}
        actual = doc['Mounts']
        check(len(actual) == len(expected_mounts) and all(m['Type'] == 'bind' and type(m['RW']) is bool and
              m.get('Propagation') == 'rprivate' for m in actual) and
              {(m['Source'], m['Destination'], m['RW']) for m in actual} == expected_mounts,
              'caddy-runtime-mounts-mismatch')
        requests = host.get('Mounts') or []
        check(len(requests) == len(expected_mounts) and all(m['Type'] == 'bind' and
              not (m.get('BindOptions') or {}).get('CreateMountpoint', False) and
              not (m.get('BindOptions') or {}).keys() - {'CreateMountpoint', 'Propagation'} and
              (m.get('BindOptions') or {}).get('Propagation', 'rprivate') == 'rprivate' for m in requests) and
              {(m['Source'], m['Target'], not m.get('ReadOnly', False)) for m in requests} == expected_mounts,
              'caddy-runtime-mount-requests-mismatch')
        networks = {artifact.model['networks'][n]['name'] for n in wanted['networks']}
        check(set(doc['NetworkSettings']['Networks']) == networks and host.get('NetworkMode') in networks,
              'caddy-runtime-networks-mismatch')
        labels = config.get('Labels') or {}
        check(all(labels.get(k) == v for k, v in wanted['labels'].items()), 'caddy-runtime-ownership-mismatch')
        return tuple(codes)
    except (KeyError, TypeError, ValueError, AttributeError):
        return ('caddy-runtime-structure-invalid',)
