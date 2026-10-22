"""Offline image-config constraints. A passing check still requires runtime proof."""
import json

from scripts.docker_adapter import DockerError

COMMAND = ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']


def inspect_caddy_contract(runtime, evidence):
    model = runtime.render().model['services']['caddy']
    if (evidence.reference != runtime.image or evidence.platform != 'linux/amd64' or evidence.verified is not True):
        raise DockerError('caddy-image-platform-or-subject-unverified')
    try:
        config = json.loads(evidence.defaults_json)
    except (ValueError, TypeError):
        raise DockerError('caddy-image-config-invalid') from None
    if (config.get('Entrypoint') not in (None, []) or config.get('Cmd') != COMMAND or
        config.get('User', '') not in ('', '0', '0:0') or config.get('WorkingDir') != '/srv' or
        not {'XDG_CONFIG_HOME=/config', 'XDG_DATA_HOME=/data', 'CADDY_VERSION=v' + runtime.version}.issubset(config.get('Env') or ()) or
        model.get('command') != COMMAND or model.get('cap_add') != ['NET_BIND_SERVICE']):
        raise DockerError('caddy-entrypoint-command-user-or-filesystem-contract-mismatch')
    return ('exact-caddy-image-provenance-required', 'isolated-caddy-read-only-capabilities-storage-test-required',
            'official-full-candidate-validation-required')


def bind_caddy_engine(runtime, evidence, inspected):
    """Docker Hub short RepoDigests are exact official-caddy name aliases only.

    Observed in run 39843536752. Digest/config/platform checks remain shared and
    unchanged. No aliasing of GHCR, other repositories, tags or mismatched digests.
    """
    from scripts.production_integration.artifacts import bind_engine_image
    inspect_caddy_contract(runtime, evidence)
    if isinstance(inspected, dict) and evidence.reference.startswith('docker.io/library/caddy@'):
        digests = inspected.get('RepoDigests')
        if isinstance(digests, list) and all(isinstance(d, str) for d in digests):
            aliases = {'caddy@' + digest: 'docker.io/library/caddy@' + digest for digest in
                       (evidence.reference.split('@')[1], evidence.manifest_digest)}
            inspected = {**inspected, 'RepoDigests': [aliases.get(d, d) for d in digests]}
    return bind_engine_image(evidence, inspected)
