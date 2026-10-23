"""Installed local sources: Engine facts matched to independently admitted contracts.

The catalog includes historical contracts, never inferred from desired state or LKG.
Unsupported delivery (config/secret refs) and ambiguous matches fail closed. No I/O
in constructors, no Docker API client, no adoption of unlisted infrastructure.
"""
from dataclasses import asdict
import time

from scripts.compose import LABEL, labels, render_compose
from scripts.deployment import preflight_resources
from scripts.docker_adapter import DockerError
from scripts.docker_discovery import discover, container_runtime, owned_resource
from scripts.gateway import make_route
from scripts.planning_inputs import load_observed_state
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import SourceResult, require
from scripts.production_integration.trust import SourceAttestation


def infrastructure_key(resource):
    facts = resource.facts
    if resource.kind == 'network':
        # Membership changes legitimately when an admitted app starts. Every
        # member is independently inventoried and classified below.
        facts.pop('containers', None)
    return resource.kind, resource.identifier, resource.name, hashed(facts)


class RuntimeInventory:
    def __init__(self, engine, contracts, resolve_image, infrastructure):
        self.engine, self.contracts, self.resolve_image = engine, tuple(contracts), resolve_image
        # Exact (kind, ID, name, facts hash) for non-application resources, approved
        # independently. Built-in networks and the gateway are not implicitly trusted.
        self.infrastructure = frozenset(infrastructure)

    def capture(self):
        identity = self.engine.identity()
        refs = {c['image'] for a in self.contracts for c in a.spec['components']}
        observation = discover(self.engine, refs, resolve_image=self.resolve_image)
        require(observation.complete and identity == self.engine.identity(), 'production-runtime-inventory-incomplete')
        require(len({(r.kind, r.identifier) for r in observation.resources}) == len(observation.resources) and
                len({(r.kind, r.name) for r in observation.resources}) == len(observation.resources),
                'production-runtime-inventory-conflicting')
        images = {i.reference: i for i in observation.images}
        require(set(images) == refs and all(i.verified for i in images.values()), 'production-runtime-images-incomplete')
        require(self.infrastructure <= {infrastructure_key(r) for r in observation.resources},
                'production-runtime-approved-infrastructure-missing-or-changed')
        apps = {a.id for a in self.contracts}
        for resource in observation.resources:
            owner = resource.facts['labels']
            if infrastructure_key(resource) in self.infrastructure:
                if resource.kind == 'network':
                    require(set(resource.facts.get('containers', {})) <=
                            {r.identifier for r in observation.resources if r.kind == 'container'},
                            'production-runtime-network-members-incomplete')
                continue
            app = owner.get(LABEL + '.application')
            require(app in apps and owner.get(LABEL + '.managed') == 'true' and
                    owner.get('com.docker.compose.project') == 'portfolio-' + app,
                    'production-runtime-foreign-resource')
        selected = []
        for app_id in sorted(apps):
            containers = [r for r in observation.resources if r.kind == 'container' and
                          r.facts['labels'].get(LABEL + '.application') == app_id]
            if not containers:
                # Network/volume leftovers still undergo preflight below, and are
                # not evidence of a previously running component.
                candidates = [a for a in self.contracts if a.id == app_id]
                require(any(not preflight_resources(a, render_compose(a), observation) for a in candidates),
                        'production-runtime-orphan-resource-conflict')
                continue
            matches = []
            for app in (a for a in self.contracts if a.id == app_id):
                spec, artifact = app.spec, render_compose(app)
                if spec['configRefs'] or spec['secretRefs'] or preflight_resources(app, artifact, observation):
                    continue
                components = spec['components']
                if len(containers) != len(components):
                    continue
                found = []
                for c in components:
                    targets = [r for r in containers if owned_resource(r, labels(app.id, c['name']), artifact.project, c['name'])]
                    if (len(targets) != 1 or not container_runtime(targets[0], artifact, c['name'], images[c['image']])):
                        break
                    found.append((c, targets[0], images[c['image']]))
                if len(found) == len(components):
                    matches.append((app, tuple(found)))
            require(len(matches) == 1, 'production-runtime-historical-contract-unknown-or-ambiguous')
            selected.append(matches[0])
        again = discover(self.engine, refs, resolve_image=self.resolve_image)
        require(again == observation and identity == self.engine.identity(), 'production-runtime-inventory-changed')
        return identity, observation, tuple(selected)


class LocalSource:
    def __init__(self, identity, engine, *, clock=time.monotonic):
        self.identity, self.engine, self.clock = identity, engine, clock
        self.last = None

    def result(self, round_id, payload):
        result = SourceResult(self.identity, round_id, self.clock(), True, True, payload)
        self.last = (result, hashed(asdict(result)), self.engine.identity())
        return result


class GatewaySource(LocalSource):
    def __init__(self, engine, port, *, postconditions=None, **kwargs):
        super().__init__('installed-active-gateway-v1', engine, **kwargs)
        self.port, self.postconditions = port, postconditions

    def collect(self, round_id):
        routes = self.port.read()
        if routes:
            require(self.postconditions is not None and
                    self.postconditions.verify(routes, self.engine.identity()) == routes,
                    'production-gateway-public-postconditions-unverified')
        return self.result(round_id, routes)


class ReadinessSource(LocalSource):
    def __init__(self, inventory, probes, **kwargs):
        super().__init__('installed-internal-readiness-v1', inventory.engine, **kwargs)
        self.inventory, self.probes = inventory, probes

    def collect(self, round_id):
        identity, _, selected = self.inventory.capture()
        health = {}
        for app, components in selected:
            for component, resource, _ in components:
                probe = self.probes[app.id].probe(component, resource.identifier, timeout=1, follow_redirects=False)
                good = probe.running is True and (type(probe.http_status) is int and
                    200 <= probe.http_status < 300 and probe.redirected is False
                    if component['type'] != 'worker' else probe.health == 'healthy')
                health[resource.identifier] = 'healthy' if good else 'unknown'
        require(identity == self.engine.identity(), 'production-readiness-engine-changed')
        return self.result(round_id, health)


class RuntimeSource(LocalSource):
    def __init__(self, inventory, gateway, readiness, **kwargs):
        super().__init__('installed-engine-runtime-v1', inventory.engine, **kwargs)
        self.inventory, self.gateway, self.readiness = inventory, gateway, readiness

    def collect(self, round_id):
        identity, _, selected = self.inventory.capture()
        routes = {r.application: r for r in self.gateway.collect(round_id).payload}
        health = self.readiness.collect(round_id).payload
        applications = []
        for app, components in selected:
            owner = {'manager': 'portfolio-deployment', 'application': app.id}
            route = routes.pop(app.id, None)
            if route is not None:
                require(app.spec['ingress'] is not None and route.key == make_route(app.id, app.spec['ingress']).key,
                        'production-runtime-route-contract-unknown')
            observed = []
            for c, resource, image in components:
                runtime = {k: v for k, v in c.items() if k not in ('name', 'image')}
                observed.append({'name': c['name'], 'existence': 'present',
                    'owner': {**owner, 'component': c['name']}, 'runtime': runtime, 'runtimeComplete': True,
                    'health': health.get(resource.identifier, 'unknown'), 'imageIdentity': {
                        'pullReference': image.reference, 'pullKind': 'manifest' if image.reference.endswith(image.manifest_digest) else 'index',
                        'manifestDigest': image.manifest_digest, 'platform': image.platform, 'relationship': 'verified'}})
            applications.append({'id': app.id, 'owner': owner, 'existence': 'present', 'components': observed,
                'storage': app.spec['storage'], 'configRefs': [], 'secretRefs': [],
                'route': {'existence': 'present' if route else 'absent', 'owner': owner,
                          'configuration': app.spec['ingress'] if route else None}})
        require(not routes and identity == self.engine.identity(), 'production-runtime-gateway-universe-unknown')
        doc = {'apiVersion': 'portfolio.observed/v1', 'scope': {'kind': 'registry', 'complete': True, 'trusted': True},
               'applications': applications}
        load_observed_state(doc)  # existing bounded closed schema
        return self.result(round_id, doc)


class LocalSourceAttestor:
    """Authenticates fixed in-process source instances, not user supplied flags.

    Installed source code/policy/Engine are the authority boundary. The exact
    returned object, immutable digest and machine/daemon are rechecked. Nothing
    here can authenticate an arbitrary port selected by a request.
    """
    def __init__(self, ports):
        self.ports = dict(ports)

    def verify(self, binding, port, result):
        require(self.ports.get(binding.source) is port and port.last is not None,
                'production-local-source-channel-unbound')
        previous, digest, identity = port.last
        require(result is previous and hashed(asdict(result)) == digest and
                identity == (binding.host, binding.daemon) == port.engine.identity(),
                'production-local-source-proof-changed')
        return SourceAttestation(binding, result)
