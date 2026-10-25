"""Shared fail-closed Engine network IPAM validation for production observations."""
import ipaddress

from scripts.docker_adapter import DockerError
from scripts.production_integration.observations import require


def network_subnets(resource):
    """Validate Engine network identity/IPAM once for policy and bootstrap.

    Only Docker's exact local host/none pairs have no allocated subnets.
    Discovery preserves IPAM.Config=null; never coerce unknown bridge IPAM away.
    """
    f = resource.facts
    require(isinstance(f, dict), 'production-host-network-ipam-unknown')
    if resource.name in ('host', 'none') or f.get('driver') in ('host', 'null'):
        require((resource.name, f.get('driver')) in (('host', 'host'), ('none', 'null')) and
                f.get('scope') == 'local', 'production-host-network-ipam-unknown')
        return ()
    require(f.get('driver') == 'bridge' and f.get('scope') == 'local' and
            isinstance(f.get('ipam'), dict) and f['ipam'].get('Driver') == 'default' and
            isinstance(f['ipam'].get('Config'), list) and bool(f['ipam']['Config']),
            'production-host-network-ipam-unknown')
    subnets = []
    try:
        for config in f['ipam']['Config']:
            if not isinstance(config, dict) or not isinstance(config.get('Subnet'), str):
                raise ValueError
            subnet = ipaddress.ip_network(config['Subnet'], strict=True)
            gateway = config.get('Gateway')
            if gateway is not None and gateway != '':
                if not isinstance(gateway, str) or ipaddress.ip_address(gateway) not in subnet:
                    raise ValueError
            subnets.append(subnet)
    except (TypeError, KeyError, ValueError):
        raise DockerError('production-host-network-ipam-unknown') from None
    return tuple(subnets)
