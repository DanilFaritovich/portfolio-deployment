"""Managed full Caddy candidate integration through an injected gateway port.

There is no live reload transport. Fake ports exercise validation, LKG retention,
CAS and unknown outcomes while Stage 06 owns release/journal/recovery decisions.
"""
from scripts.deployment_lifecycle import DeploymentLifecycle
from scripts.docker_adapter import DockerError
from scripts.gateway import build_gateway_candidate, render_caddyfile, route_set
from scripts.reconciliation import build_reconciliation_plan


def content(routes):
    rendered = render_caddyfile(route_set(routes))
    if rendered is None:
        raise DockerError('production-gateway-no-routes')
    return '{\n    admin 127.0.0.1:2019\n}\n\n' + rendered


class ProductionGateway:
    """port: read(), validate(text), preserve_lkg(text), compare_and_reload(old,new).

    A trusted runtime attestor must verify pinned image/config ID, version,
    ownership, ports, storage permissions and exclusive gateway ownership before
    giving this controller a port. No port is supplied by ordinary CLI/CI.
    read() returns independently observed managed GatewayRoutes. Foreign config
    must raise, never be converted into an empty route set.
    """
    def __init__(self, port):
        self.port = port

    def activate(self, desired, observed, application, *, ready, lease):
        if ready is not True or lease is None or lease.active is not True:
            raise DockerError('production-gateway-ready-lock-required')
        plan = build_reconciliation_plan(desired, observed)
        candidate = build_gateway_candidate(desired, observed, plan).document
        if candidate['status'] in ('blocked', 'degraded'):
            raise DockerError('production-gateway-candidate-blocked')
        # Only target changes. Retain ALL actual unrelated routes, including old
        # contracts when another desired app is pending a separate rollout.
        from scripts.gateway import desired_routes, make_route
        target = next((r for r in desired_routes(desired) if r.application == application), None)
        if target is None:
            raise DockerError('production-gateway-target-unavailable')
        before = route_set(self.port.read())
        expected = route_set(make_route(a['id'], a['route']['configuration'])
                             for a in observed.document['applications'] if a['route']['existence'] == 'present')
        if tuple(r.key for r in before) != tuple(r.key for r in expected):
            raise DockerError('production-gateway-stale-or-foreign')
        after = route_set([r for r in before if r.application != application] + [target])
        old_text, new_text = content(before) if before else None, content(after)
        if old_text == new_text:
            return False  # image-only update never validates/reloads/restarts Caddy
        self.port.validate(new_text)  # full official validation required by port
        if old_text is not None:
            self.port.preserve_lkg(old_text)  # durable before reload; never replace on failure
        if route_set(self.port.read()) != before:
            raise DockerError('production-gateway-state-changed')
        self.port.compare_and_reload(old_text, new_text)
        if tuple(r.key for r in route_set(self.port.read())) != tuple(r.key for r in after):
            raise DockerError('production-gateway-activation-unverified', True)
        return True


class RehearsalLifecycle(DeploymentLifecycle):
    """Exercise production sequencing using SAME executor and Stage06 test gate.

    Kept deliberately test-only: inherited open rejects production adapters. A
    Stage09.4 change must implement reviewed authorization rather than passing a
    fabricated test session. Gateway failures cannot promote application LKG.
    """
    def __init__(self, *args, gateway, **kwargs):
        super().__init__(*args, **kwargs)
        self.gateway = gateway

    def complete(self, results):
        if all(r.status == 'succeeded' for r in results):
            try:
                pinned = {}
                for name in sorted(self.verified):
                    resource, _ = self._fresh_target(self.app, name)
                    pinned[name] = resource.identifier
                    if self._verify(self.app, name).status != 'verified':
                        raise DockerError('readiness-failed')
                snapshot = self.provider.refresh()
                for name, identity in pinned.items():
                    resource, _ = self._fresh_target(self.app, name, snapshot=snapshot)
                    if resource.identifier != identity:
                        raise DockerError('gateway-readiness-identity-changed')
                if self.app.spec['ingress'] is not None:
                    self.gateway.activate(self.desired, snapshot[1], self.app.id,
                                          ready=True, lease=self.journal.lease)
                    self.provider.refresh()  # full post-activation evidence before LKG
            except Exception:
                from dataclasses import replace
                self.journal.fail_run(self.run_id, 'gateway-unverified')
                return [replace(r, status='recovery-required', reasons=('gateway-unverified',)) for r in results]
        return super().complete(results)
