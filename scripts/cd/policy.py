"""Operator-signed standing permission; release requests cannot select authority.

The bootstrap independently pins operator keys, this file and its complete capsule.
Changing policy is an administrative operation, never an automation capability.
"""
from dataclasses import asdict
from pathlib import Path
import hashlib
import re
import time

from scripts.cd.signatures import authenticate, POLICY_NAMESPACE
from scripts.production_integration.authorization import review_approval
from scripts.production_integration.installation import protected
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import authenticate_signed

POLICY = Path('/etc/portfolio-deployment/cd-policy.json')
FIELDS = {'version', 'id', 'host', 'daemon', 'not_before', 'expires', 'initial_manager_sha',
          'automation', 'observer', 'manager', 'application', 'host_contract', 'deployment_workflows'}
FROZEN_BOUNDARY = frozenset({'bindings.py', 'launcher.py', 'cd_entry.py', 'executor.service',
    'scripts/deployment_journal.py', 'manager/policy/admission.yaml',
    *('scripts/production_integration/'+n+'.py' for n in
      ('installation','operator_approval','fixed_bindings','phase_bindings','trust')),
    *('scripts/cd/'+n+'.py' for n in ('policy','signatures','context','application','host'))})


def pin(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class StandingPolicy:
    def __init__(self, raw, *, operator_keys, reader=None, clock=time.time):
        self.raw, self.keys, self.reader, self.clock = raw, operator_keys, reader, clock
        self.document, self.operator = authenticate(raw, keys=operator_keys, namespace=POLICY_NAMESPACE)
        from scripts.production_integration.operator_approval import canonical
        self.payload_bytes = canonical(self.document)
        self.validate()

    def validate(self):
        d = self.document
        require(type(d) is dict and set(d) in (FIELDS, FIELDS | {'initial_journal'}) and
                d['version'] == 1, 'cd-policy-fields-invalid')
        require('initial_journal' not in d or d['initial_journal'] == 'reviewed-empty-host',
                'cd-initial-journal-policy-invalid')
        require(type(d['id']) is str and re.fullmatch(r'[0-9a-f]{32}', d['id']) and
                re.fullmatch(r'[0-9a-f]{32}', d['host']) and
                type(d['daemon']) is str and 0 < len(d['daemon']) <= 128 and
                re.fullmatch(r'[0-9a-f]{40}', d['initial_manager_sha']), 'cd-policy-identity-invalid')
        require(all(type(d[k]) is int for k in ('not_before', 'expires')) and
                d['not_before'] <= self.clock() < d['expires'] and
                0 < d['expires'] - d['not_before'] <= 366 * 86400, 'cd-policy-expired')
        for role in ('automation', 'observer'):
            entry = d[role]
            require(type(entry) is dict and set(entry) == {'principal', 'key'}, 'cd-policy-role-invalid')
            from scripts.production_integration.operator_approval import canonical, trust_keys
            trust_keys(canonical({'version': 1, 'operators': {entry['principal']: entry['key']}}))
        require(d['automation']['key'] != d['observer']['key'] and
                d['automation']['principal'] != d['observer']['principal'] and
                d['automation']['key'] not in self.keys.values() and d['observer']['key'] not in self.keys.values(),
                'cd-identities-must-be-separate')
        m = d['manager']
        require(type(m) is dict and set(m) == {'assets', 'frozen', 'checks', 'workflow', 'workflow_sha256', 'bundle_name'} and
                m['bundle_name'] == 'portfolio-deployment.zip' and
                m['workflow'] == '.github/workflows/manager-release.yml' and digest(m['workflow_sha256']) and
                type(m['assets']) is dict and bool(m['assets']) and type(m['frozen']) is dict and
                FROZEN_BOUNDARY <= m['frozen'].keys() and
                all(digest(v) for v in (*m['assets'].values(), *m['frozen'].values())), 'cd-manager-policy-incomplete')
        a = d['application']
        require(type(a) is dict and set(a) == {'template_sha256', 'workflow_sha256', 'checks', 'publication',
                                             'slsa_builder', 'initial_release'} and
                digest(a['template_sha256']) and digest(a['workflow_sha256']), 'cd-application-policy-invalid')
        from scripts.production_integration.release_source import WorkflowCheck
        for item in [*m['checks'], *a['checks'], a['publication']]:
            WorkflowCheck(**item).validate()
        require(type(d['host_contract']) is dict, 'cd-host-policy-missing')
        require(type(d['deployment_workflows']) is dict and set(d['deployment_workflows']) ==
                {'DanilFaritovich/portfolio-deployment', 'DanilFaritovich/project-router'} and
                all(type(v) is dict and set(v) == {'path', 'sha256'} and digest(v['sha256']) and
                    re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.yml', v['path'])
                    for v in d['deployment_workflows'].values()), 'cd-deployment-workflow-policy-invalid')

    def current(self):
        from scripts.production_integration.operator_approval import canonical
        require(canonical(self.document) == self.payload_bytes, 'cd-policy-payload-changed')
        self.validate()
        if self.reader is not None:
            require(self.reader() == self.raw, 'cd-policy-revoked-or-replaced')
        return self

    @property
    def identity(self):
        return 'sha256:' + pin(self.raw)

    def operation(self, candidate, envelope):
        self.current()
        d = self.document
        require((candidate.host, candidate.daemon) == (d['host'], d['daemon']) and
                candidate.scope in ('update-project-router', 'prepare-project-router-image'), 'cd-scope-not-authorized')
        # The normal candidate binds the exact signed-policy hash in HostContracts;
        # the independently installed preflight rechecks the policy under lock.
        role = d['automation']
        class Verifier:
            def authenticate(_, raw):
                self.current()
                claims = authenticate_signed(raw, {role['principal']: role['key']})
                require(claims.expires - claims.not_before <= 300, 'cd-approval-lifetime-exceeded')
                return claims
        return review_approval(candidate, envelope, verifier=Verifier(), now=self.clock())


class AutomationVerifier:
    def __init__(self, policy, candidate):
        self.policy, self.candidate = policy, candidate

    def authenticate(self, envelope):
        return self.policy.operation(self.candidate, envelope)


def read_policy(operator_keys):
    def reader():
        protected(POLICY)
        require(POLICY.stat().st_size <= 131072, 'cd-policy-too-large')
        return POLICY.read_bytes()
    return StandingPolicy(reader(), operator_keys=operator_keys, reader=reader)
