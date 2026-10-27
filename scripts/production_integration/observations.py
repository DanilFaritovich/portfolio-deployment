"""Fresh production source composition, independently corroborated with Engine.

Ports are trusted implementations, not booleans loaded from inspection reports.
No production port is configured by the CLI. Samples cannot authorize execution.
"""
from dataclasses import dataclass
import time
import uuid

from scripts.compose import LABEL, labels, render_compose
from scripts.composite_state import CompositeSample, CompositeStateProvider, SourceEvidence
from scripts.deployment import preflight_resources
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import discover, container_runtime, owned_resource
from scripts.gateway import make_route, route_set
from scripts.planning_inputs import load_observed_state
from scripts.reconciliation import DesiredApplication, encode


@dataclass(frozen=True)
class SourceResult:
    identity: str
    round_id: str
    captured_at: float
    complete: bool
    verified: bool
    payload: object
    error: str | None = None


def require(condition, reason):
    if not condition:
        raise DockerError(reason)


class ProductionStateReader:
    """runtime port supplies complete delivery inventory incl. OLD managed specs.

    Runtime payload is a Stage 03 document, gateway payload is tuple[GatewayRoute],
    readiness payload maps actual container ID to Stage 03 health. Journal/LKG
    and Stage09.2 reports are deliberately not inputs. Each port must obtain its
    state in collect(round_id), using bounded authenticated read transports.
    Double inventory and identity checks detect critical races; executor repeats
    refresh under the SAME host lock before mutation. No atomic host snapshot is
    claimed across independent processes.
    """
    def __init__(self, desired_reader, docker, runtime, gateway, readiness, resolve_image,
                 proxy_contract, *, clock=time.monotonic, source_trust=None):
        self.desired_reader, self.docker = desired_reader, docker
        self.ports = {'runtime': runtime, 'gateway': gateway, 'readiness': readiness}
        self.resolve_image, self.proxy_contract, self.clock = resolve_image, proxy_contract, clock
        self.source_trust = source_trust

    def provider(self):
        return CompositeStateProvider(self, clock=self.clock)

    def __call__(self):
        from scripts.production_integration.runtime import validate_proxy
        start, round_id = self.clock(), uuid.uuid4().hex
        identity = self.docker.identity()
        require(self.source_trust is not None, 'production-source-trust-unconfigured')
        desired = self.desired_reader()  # re-admission, not a cached desired object
        desired_refs = {c['image'] for a in desired.applications for c in a.spec['components']}
        # Installed dependent sources share only the inventory collected in this
        # call. The Engine corroboration remains independent of their payloads.
        from scripts.production_integration.runtime_sources import RuntimeSource
        runtime = self.ports['runtime']
        before = None
        if type(runtime) is RuntimeSource:
            require(runtime.gateway is self.ports['gateway'] and runtime.readiness is self.ports['readiness'],
                    'production-source-round-unbound')
            results, before = runtime.collect_round(round_id, lambda refs: discover(
                self.docker, desired_refs | refs, resolve_image=self.resolve_image))
        else:
            results = {name: port.collect(round_id) for name, port in self.ports.items()}
        now = self.clock()
        for name, result in results.items():
            require(isinstance(result, SourceResult) and bool(result.identity) and
                    result.round_id == round_id and result.complete is True and result.verified is True and
                    result.error is None and start <= result.captured_at <= now and now - result.captured_at <= 5,
                    'production-source-incomplete-or-unverified')
            self.source_trust.verify(name, identity, self.ports[name], result)
        observed = load_observed_state(results['runtime'].payload)
        require(observed.document['scope'] == {'kind': 'registry', 'complete': True, 'trusted': True},
                'production-runtime-scope-incomplete')
        refs = set(desired_refs)
        for app in observed.document['applications']:
            for c in app['components']:
                if c['imageIdentity']:
                    refs.add(c['imageIdentity']['pullReference'])
        if before is None:
            before = discover(self.docker, refs, resolve_image=self.resolve_image)
        require(before.complete, 'production-docker-discovery-incomplete')
        validate_proxy(before, self.proxy_contract)
        require({i.reference for i in before.images} == refs and all(i.verified for i in before.images),
                'production-artifact-inventory-incomplete')
        for app in desired.applications:
            require(not preflight_resources(app, render_compose(app), before), 'production-resource-preflight-failed')
        images = {i.reference: i for i in before.images}
        routes = route_set(results['gateway'].payload)
        route_by_app = {r.application: r for r in routes}
        apps = {a.id: a for a in desired.applications}
        # Unknown managed applications cannot disappear from a complete snapshot.
        observed_apps = {a['id']: a for a in observed.document['applications']}
        require(all((r.facts['labels'].get(LABEL + '.application') in observed_apps or
                     (r.kind != 'container' and r.facts['labels'].get(LABEL + '.application') in apps))
                    for r in before.resources if r.facts['labels'].get(LABEL + '.managed') == 'true' and
                    r.facts['labels'].get(LABEL + '.application')), 'production-managed-universe-incomplete')
        for app in observed.document['applications']:
            require(app['id'] in apps, 'production-unreviewed-managed-application')
            wanted_app = apps[app['id']]
            spec = wanted_app.spec
            components = []
            actual_containers = [r for r in before.resources if r.kind == 'container' and
                                 r.facts['labels'].get('com.docker.compose.project') == 'portfolio-' + app['id']]
            require(len(actual_containers) == len(app['components']) and
                    {r.facts['labels'].get('com.docker.compose.service') for r in actual_containers} ==
                    {c['name'] for c in app['components']}, 'production-component-inventory-mismatch')
            for c in app['components']:
                require(c['existence'] == 'present' and c['runtimeComplete'] is True and c['runtime'] is not None and
                        c['imageIdentity'] is not None, 'production-component-unverified')
                components.append({'name': c['name'], 'image': c['imageIdentity']['pullReference'], **c['runtime']})
            if components:
                spec['components'] = components
                # Old route/runtime may differ from desired. Never copy desired into observed.
                spec['ingress'] = app['route']['configuration']
                spec['storage'] = app['storage']
                old = DesiredApplication(app['id'], wanted_app.source_repository, encode(spec))
                artifact = render_compose(old)
                require(not preflight_resources(old, artifact, before), 'production-resource-preflight-failed')
                for c in app['components']:
                    found = [r for r in before.resources if r.kind == 'container' and
                             r.facts['labels'].get('com.docker.compose.project') == artifact.project and
                             r.facts['labels'].get('com.docker.compose.service') == c['name']]
                    image = images.get(c['imageIdentity']['pullReference'])
                    require(image is not None and c['imageIdentity']['relationship'] == 'verified' and
                            c['imageIdentity']['manifestDigest'] == image.manifest_digest and
                            c['imageIdentity']['platform'] == image.platform, 'production-image-identity-mismatch')
                    require(len(found) == 1 and image is not None and image.verified and
                            owned_resource(found[0], labels(app['id'], c['name']), artifact.project, c['name']) and
                            container_runtime(found[0], artifact, c['name'], image), 'production-effective-runtime-unverified')
                    require(c['health'] != 'healthy' or found[0].facts['state'].get('Status') == 'running',
                            'production-readiness-runtime-contradiction')
                    require(results['readiness'].payload.get(found[0].identifier) == c['health'],
                            'production-readiness-identity-mismatch')
            route = route_by_app.pop(app['id'], None)
            require((route is None and app['route']['existence'] == 'absent') or
                    (route is not None and app['route']['existence'] == 'present' and
                     route.key == make_route(app['id'], app['route']['configuration']).key),
                    'production-gateway-observation-mismatch')
        require(not route_by_app, 'production-gateway-universe-incomplete')
        after = discover(self.docker, refs, resolve_image=self.resolve_image)
        require(before == after and identity == self.docker.identity(), 'production-state-changed-during-observation')
        evidence = tuple(SourceEvidence(name, round_id, r.captured_at, True, True) for name, r in results.items())
        return CompositeSample(desired, observed, before,
                               (*evidence, SourceEvidence('docker', round_id, self.clock(), True, True)),
                               gateway_verified=all(c['health'] == 'healthy' for a in observed.document['applications'] for c in a['components']))
