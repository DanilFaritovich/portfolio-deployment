"""Inspection orchestration and conservative readiness assessment; no executor imports."""

from datetime import datetime, timezone
import ipaddress

from scripts.production import InspectionError
from scripts.production.probes import COMMANDS, CATEGORIES, normalize

UNVERIFIED = [
    'Independent provenance of the initial SSH host trust requires operator verification.',
    'Public SSH/HTTP/HTTPS reachability, DNS, ACME and GHCR access are unverified.',
    'Protected exact-SHA source, origin-bound image digest and OCI platform mapping are unverified.',
    'Approved deployment/journal/lock/Caddy paths, ownership and backup/recovery policy are required.',
    'Approved proxy subnet allocation and measured workload/disk/RAM budget are required.',
    'Stage 09.3 runtime hardening, production adapters and full fresh observation remain required.',
]
LIMITATIONS = [
    'Read-only snapshot; not a complete or trusted ObservedState and never execution authorization.',
    'Only rootful local unix:///var/run/docker.sock is inspected; no context changes or rootless fallback.',
    'Ownership labels are claims, not proof; foreign resources are preserved.',
    'Unprivileged ss omits process attribution; firewall visibility may be unavailable.',
    'No server configuration, credentials, home content, container env/mounts or raw stderr retained.',
    'No sudo, package installation, image pulls, mutation, host-trust repair or retries.',
    'Read-only commands can cause ordinary SSH/authentication audit logs and access timestamps.',
]


def now():
    return datetime.now(timezone.utc).isoformat()


def inspect(target, transport, trusted_fingerprints=()):
    started = now()
    checks, conflicts = [], []
    stopped = None
    for name in COMMANDS:
        item = {'name': name, 'category': CATEGORIES[name], 'status': 'unknown', 'reason': 'not-observed', 'details': {}}
        if stopped:
            item['reason'] = 'transport-stopped'
        else:
            try:
                reply = transport.probe(name)
                if len(reply.stdout) + len(reply.stderr) > 262144:
                    raise InspectionError('transport-output-limit')
                if reply.code:
                    item['reason'] = ('docker-permission-denied' if name in ('engine', 'daemon', 'containers', 'networks', 'volumes')
                                      and b'permission denied' in reply.stderr.lower() else 'command-not-installed' if reply.code == 127 else 'probe-unavailable-or-denied')
                    if name in ('engine', 'daemon', 'compose', 'compose_cli'):
                        item['status'] = 'blocked'
                else:
                    item['details'] = normalize(name, reply.stdout)
                    item.update(status='passed', reason='observed')
            except InspectionError as error:
                # Both real and fake transports return only fixed safe error codes.
                code = str(error)
                safe = {'host-key-mismatch', 'ssh-authentication-failed', 'ssh-connection-timeout',
                        'ssh-network-or-transport-failure', 'transport-timeout', 'transport-output-limit',
                        'transport-unavailable', 'invalid-or-incomplete-probe-response'}
                item['reason'] = code if code in safe else 'transport-preflight-failed'
                if code != 'invalid-or-incomplete-probe-response':
                    stopped = item['reason']
                    item['status'] = 'blocked'
        checks.append(item)
    by_name = {c['name']: c for c in checks}
    for name, valid in (
        ('os', lambda d: d == {'id': 'ubuntu', 'version_id': '24.04'}),
        ('architecture', lambda d: d['value'] == 'x86_64'),
        ('listeners', lambda d: target.port in d['ports']),
    ):
        item = by_name[name]
        if item['status'] == 'passed' and not valid(item['details']):
            item.update(status='blocked', reason='production-contract-mismatch')
    for name in ('containers', 'networks', 'volumes'):
        item = by_name[name]
        for resource in item['details'].get('resources', []):
            if (resource['name'] in ('portfolio-proxy', 'portfolio-project-router-private', 'portfolio-project-router') or
                    resource.get('project') == 'portfolio-project-router' or
                    resource['name'].startswith('portfolio-project-router-')):
                conflicts.append(f"{name}:{resource['name']}:reserved-resource-requires-review")
                item.update(status='blocked', reason='reserved-resource-conflict')
    networks = by_name['networks']
    resources = networks['details'].get('resources', [])
    for index, left in enumerate(resources):
        for right in resources[index + 1:]:
            if any(a.version == b.version and a.overlaps(b)
                   for a in map(ipaddress.ip_network, left['subnets'])
                   for b in map(ipaddress.ip_network, right['subnets'])):
                conflicts.append(f"networks:{left['name']}:{right['name']}:overlapping-subnets")
                networks.update(status='blocked', reason='subnet-overlap')
    listeners = by_name['listeners']
    for port in (80, 443, 2375, 2376):
        if port in listeners['details'].get('ports', []):
            conflicts.append(f'listeners:{port}:occupied')
            listeners.update(status='blocked', reason='reserved-or-docker-api-port-occupied')
    services = by_name['services']
    for service in services['details'].get('services', []):
        if service['Id'] != 'docker.service' and service['ActiveState'] == 'active':
            conflicts.append('services:' + service['Id'] + ':existing-reverse-proxy')
            services.update(status='blocked', reason='existing-reverse-proxy')
    if len(set(conflicts)) > 128:
        conflicts = sorted(set(conflicts))[:127] + ['conflict-list-truncated:review-full-resource-evidence']
    blockers = [c['name'] + ':' + c['reason'] for c in checks if c['status'] != 'passed'] + UNVERIFIED[2:]
    identity = {name: by_name[name]['details'].get('value') for name in ('hostname', 'machine_id', 'daemon')}
    return {'schemaVersion': 'portfolio.production-inspection/v1', 'startedAt': started, 'finishedAt': now(),
            'target': target.document, 'identity': identity,
            'sshTrust': {'policy': 'strict-existing-user-known-hosts', 'initialProvenance': 'operator-verification-required',
                         'negotiatedKey': 'not-collected', 'trustedFingerprints': list(trusted_fingerprints)},
            'checks': checks,
            'conflicts': sorted(set(conflicts))[:128], 'unverifiedPrerequisites': UNVERIFIED,
            'blockers': blockers, 'limitations': LIMITATIONS}
