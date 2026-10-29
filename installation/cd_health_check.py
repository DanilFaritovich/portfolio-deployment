"""Capsule-owned isolated read-only Manager health probe; never starts execution."""
import json
from pathlib import Path
import sys

root = Path('/opt/portfolio-deployment')
sys.path[0:0] = [str(root), str(root / 'vendor')]
try:
    from scripts.production_integration.installation import SealedInstallation
    document = json.loads((root / 'installation.json').read_bytes())
    installation = SealedInstallation(document['manager_sha'], tuple(map(tuple, document['files'])))
    installation.verify()
    import scripts.cd.application
    import scripts.production_integration.bootstrap
    import scripts.production_integration.composition
    import bindings
    from scripts.validate_registry import validate_registry
    validate_registry(root / 'manager')
    if (root/'scripts/production_integration/network_inventory.py').exists():
        from scripts.production_integration.network_inventory import network_subnets
        from scripts.docker_discovery import DockerResource
        for name,driver in (('host','host'),('none','null')):
            resource=DockerResource('network',name,name,json.dumps(
                {'driver':driver,'scope':'local','ipam':{'Driver':'default','Config':None}}))
            if network_subnets(resource)!=():
                raise ValueError('manager-ipam-regression')
    print(installation.manager_sha)
except Exception:
    sys.exit(2)
