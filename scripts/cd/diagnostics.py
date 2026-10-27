"""Closed, public failure codes. Never serialize arbitrary exception text."""
from scripts.docker_adapter import DockerError

PUBLIC_FAILURES = {
    'cd-journal-recovery-required': 'an unfinished journal operation blocks Manager updates; use separately reviewed operator recovery; do not replay bootstrap',
    'cd-journal-history-lost': 'established journal history is missing; preserve state and inspect through the operator channel',
    'cd-policy-expired': 'the operator-signed standing policy expired; an administrator must review its renewal',
    'production-review-expired': 'the administrative phase review expired; an administrator must review its renewal',
    'production-review-phase-or-source-invalid': 'the phase review does not match the installed Manager or permitted phase',
    'production-installation-content-changed': 'sealed installation content changed; inspect independently before delivery',
    'production-storage-content-changed': 'protected storage changed during verification; preserve the journal and inspect before recovery',
    'production-bootstrap-postconditions-changed-or-expired': 'bootstrap postconditions changed or exceeded their observation window; preserve state; do not replay',
    'production-bootstrap-recovery-baseline-mismatch': 'retained Docker resources differ from the independently reviewed empty-host baseline; recovery remains blocked',
    'production-approval-stale-or-binding-invalid': 'operator approval expired or does not bind the exact candidate',
    'production-operator-approval-invalid': 'operator approval signature or document is invalid',
    'production-exact-candidate-changed': 'fresh preflight produced a different candidate; review a new candidate',
    'production-release-unverified': 'the production release proof did not verify',
    'production-release-api-timeout': 'the release evidence API timed out',
    'production-release-api-timeout-or-unavailable': 'the release evidence API timed out or was unavailable',
    'production-release-budget-exhausted': 'the release evidence collection budget expired',
    'production-active-config-changed': 'the active gateway observation changed or exceeded its bounded window',
    'production-active-config-evidence-expired-or-changed': 'the active gateway evidence expired or changed',
    'production-host-source-changed-or-expired': 'the host observation changed or expired',
    'production-host-source-deadline': 'the host observation exceeded its deadline',
    'production-joint-observation-timeout': 'the coordinated observation exceeded its deadline',
    'production-joint-source-expired': 'a coordinated source observation expired',
    'production-preflight-state-changed': 'state changed during preflight',
    'production-plan-preflight-blocked': 'the deployment plan did not pass preflight',
    'production-gateway-preflight-blocked': 'the gateway candidate did not pass preflight',
    'production-execution-dependency-unavailable': 'an execution dependency failed before the outcome could be established; inspect receipts',
}


def public_code(error):
    code = error.code if isinstance(error, DockerError) else None
    return code if type(code) is str and code in PUBLIC_FAILURES else None


def blocked(error, reason):
    result = {'status': 'blocked', 'reason': reason}
    code = public_code(error)
    if code is not None:
        result['diagnostic'] = code
    return result


def remote_code(document):
    code = document.get('diagnostic')
    if type(code) is str and code in PUBLIC_FAILURES:
        return code
    reasons = document.get('reasons')
    if type(reasons) in (list, tuple) and len(reasons) <= 2:
        known = [c for c in reasons if type(c) is str and c in PUBLIC_FAILURES]
        if len(known) == 1:
            return known[0]
    return None
