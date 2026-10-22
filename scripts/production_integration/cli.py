"""Offline operator preparation; contracts validate but never grant write access."""
import argparse
from dataclasses import fields

from scripts.docker_adapter import DockerError
from scripts.gateway import build_gateway_candidate
from scripts.planning_inputs import build_desired_state, load_observed_state, parse_snapshot, read_snapshot
from scripts.production_integration.runtime import CaddyRuntime, ProxyContract, RuntimePaths
from scripts.reconciliation import PlanningInputError, build_reconciliation_plan, encode
from scripts.validate_registry import RegistryValidationError

BLOCKERS = ('stage-09-4-operation-authorization-unavailable', 'fresh-host-lock-observation-required',
            'protected-main-exact-sha-ci-provenance-verifier-required', 'accepted-inspection-and-operator-decisions-required',
            'gateway-runtime-image-version-ownership-attestation-required', 'production-readiness-transport-required', 'read-only-image-compatibility-evidence-required')


def closed(value, cls):
    if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
        raise DockerError('production-contract-unknown-or-missing-fields')
    return cls(**value)


def validate_contract(document):
    if (type(document) is not dict or set(document) != {'apiVersion', 'paths', 'proxy', 'caddy'} or
        document['apiVersion'] != 'portfolio.production-runtime/v1'):
        raise DockerError('production-contract-version-or-fields-invalid')
    paths = closed(document['paths'], RuntimePaths)
    paths.validate()
    proxy = closed(document['proxy'], ProxyContract)
    proxy.validate()
    caddy = document['caddy']
    if type(caddy) is not dict or set(caddy) != {'image', 'version'}:
        raise DockerError('production-caddy-contract-invalid')
    runtime = CaddyRuntime(caddy['image'], caddy['version'], paths)
    return runtime, proxy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'plan'))
    parser.add_argument('--contract', required=True)
    parser.add_argument('--root', default='.')
    parser.add_argument('--git-revision')
    parser.add_argument('--observed')
    args = parser.parse_args(argv)
    try:
        runtime, proxy = validate_contract(parse_snapshot(read_snapshot(args.contract)))
        artifact = runtime.render()
        result = {'apiVersion': 'portfolio.production-preview/v1', 'status': 'blocked',
                  'executed': False, 'authorization': False, 'previewOnly': True,
                  'blockers': list(BLOCKERS), 'gatewayRuntime': artifact.model,
                  'officialCaddyValidation': 'not-performed'}
        if args.command == 'plan':
            if not args.observed or not args.git_revision:
                raise DockerError('production-explicit-snapshot-and-revision-required')
            desired = build_desired_state(args.root, args.git_revision)
            observed = load_observed_state(args.observed)
            plan = build_reconciliation_plan(desired, observed)
            result['plan'] = plan.document
            result['gateway'] = build_gateway_candidate(desired, observed, plan).document
            for app in desired.applications:
                for c in app.spec['components']:
                    if 'runtimeSecurity' not in c:
                        result['blockers'].append('production-explicit-runtime-security-required')
        print(encode(result))
        return 2  # a syntactically valid contract is still not deployment approval
    except (DockerError, PlanningInputError, RegistryValidationError, OSError, TypeError, ValueError, KeyError):
        print(encode({'status': 'blocked', 'executed': False, 'authorization': False,
                      'blockers': ['production-offline-input-invalid-or-unavailable']}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
