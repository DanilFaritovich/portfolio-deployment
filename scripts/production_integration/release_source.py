"""Source policy over authenticated GitHub REST responses; no API booleans of trust."""
from dataclasses import dataclass
import re

from scripts.docker_adapter import DockerError
from scripts.production_integration.release import PRODUCER, MANAGER
from scripts.production_integration.release_transport import require, bounded_items


@dataclass(frozen=True)
class WorkflowCheck:
    repository: str
    name: str
    app_id: int
    workflow: str
    event: str = 'push'

    def validate(self):
        require(self.repository in (PRODUCER, MANAGER) and type(self.app_id) is int and self.app_id > 0 and
                isinstance(self.name, str) and 0 < len(self.name) <= 128 and
                re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.ya?ml', self.workflow) is not None and
                self.event == 'push', 'production-release-workflow-policy-invalid')


class ProtectedSource:
    def __init__(self, github):
        self.github = github

    def policy(self, repo, required):
        api = lambda path: self.github.get(repo, path)
        # Effective-branch endpoint establishes applicability, including inherited
        # rules. Do not guess glob matching or treat evaluate/disabled as active.
        rules = bounded_items(api('/rules/branches/main?per_page=100'))
        classic = None
        try:
            classic = api('/branches/main/protection')
        except DockerError as error:
            if str(error) != 'production-release-api-not-found':
                raise
        rule_checks = set()
        types = set()
        ids = set()
        for rule in rules:
            require(isinstance(rule, dict) and type(rule['ruleset_id']) is int and rule['ruleset_id'] > 0)
            ids.add(rule['ruleset_id'])
            types.add(rule['type'])
            if rule['type'] == 'required_status_checks':
                parameters = rule['parameters']
                require(parameters['strict_required_status_checks_policy'] is True,
                        'production-release-protection-insufficient')
                for check in bounded_items(parameters['required_status_checks']):
                    require(type(check['integration_id']) is int and check['integration_id'] > 0,
                            'production-release-protection-app-unbound')
                    rule_checks.add((check['context'], check['integration_id']))
        require(len(ids) <= 10, 'production-release-protection-unverifiable')
        for identifier in sorted(ids):
            # Never follow _links.self; the repository endpoint also resolves
            # applicable parent rulesets. Denied/incomplete details block.
            detail = api('/rulesets/' + str(identifier))
            require(detail['id'] == identifier and detail['enforcement'] == 'active' and
                    detail['target'] == 'branch' and detail['bypass_actors'] == [],
                    'production-release-protection-unverifiable')
        classic_checks = set()
        strong_classic = False
        if classic is not None:
            checks = classic.get('required_status_checks')
            reviews = classic.get('required_pull_request_reviews')
            if isinstance(checks, dict):
                for check in bounded_items(checks['checks']):
                    require(type(check['app_id']) is int and check['app_id'] > 0,
                            'production-release-protection-app-unbound')
                    classic_checks.add((check['context'], check['app_id']))
            strong_classic = bool(isinstance(checks, dict) and checks.get('strict') is True and
                isinstance(reviews, dict) and type(reviews.get('required_approving_review_count')) is int and
                reviews['required_approving_review_count'] >= 1 and reviews.get('dismiss_stale_reviews') is True and
                reviews.get('bypass_pull_request_allowances') == {'users': [], 'teams': [], 'apps': []} and
                classic.get('enforce_admins', {}).get('enabled') is True and
                classic.get('allow_force_pushes', {}).get('enabled') is False and
                classic.get('allow_deletions', {}).get('enabled') is False)
        # Narrow policy: PR review + no force update/deletion + app-bound checks.
        # Ruleset PR parameters are checked separately; unsupported policy blocks.
        strong_rules = {'pull_request', 'non_fast_forward', 'deletion', 'required_status_checks'} <= types
        if strong_rules:
            for rule in rules:
                if rule['type'] == 'pull_request':
                    p = rule['parameters']
                    require(type(p['required_approving_review_count']) is int and
                            p['required_approving_review_count'] >= 1 and p['dismiss_stale_reviews_on_push'] is True,
                            'production-release-protection-insufficient')
        require(strong_classic or strong_rules, 'production-release-protection-insufficient')
        enforced = (classic_checks if strong_classic else set()) | (rule_checks if strong_rules else set())
        require(required <= enforced, 'production-release-protection-insufficient')
        effective = classic_checks | rule_checks
        require(required <= effective and effective, 'production-release-required-check-policy-mismatch')
        # Validate every effective mandatory check, including additions made by
        # repository policy; unknown workflows cannot be silently ignored.
        require(effective == required, 'production-release-required-check-policy-mismatch')

    def collect(self, repo, sha, checks, publish=None, signer_sha=None, signer_workflow=None):
        api = lambda path: self.github.get(repo, path)
        metadata = api('')
        require(metadata['full_name'] == repo and type(metadata['id']) is int and metadata['id'] > 0)
        branch = api('/branches/main')
        head = branch['commit']['sha']
        require(branch['name'] == 'main' and re.fullmatch(r'[0-9a-f]{40}', head) is not None)
        comparison = api('/compare/' + sha + '...' + head)
        require(comparison['status'] in ('ahead', 'identical') and
                comparison['merge_base_commit']['sha'] == sha and comparison['base_commit']['sha'] == sha,
                'production-release-sha-not-on-main')
        self.policy(repo, {(c.name, c.app_id) for c in checks})
        runs = bounded_items(api('/actions/runs?head_sha=' + sha + '&per_page=100'), 'workflow_runs')
        def run_identity(run, workflow, event):
            return (type(run.get('id')) is int and run['id'] > 0 and
                    type(run.get('run_attempt')) is int and run['run_attempt'] > 0 and
                    run.get('head_sha') == sha and
                    isinstance(run.get('head_branch'), str) and bool(run['head_branch']) and
                    (event == 'pull_request' or run['head_branch'] == 'main') and
                    run.get('path') == workflow and run.get('event') == event and
                    run.get('repository', {}).get('id') == metadata['id'] and
                    run.get('repository', {}).get('full_name') == repo and
                    run.get('head_repository', {}).get('id') == metadata['id'] and
                    run.get('head_repository', {}).get('full_name') == repo)
        def valid_run(run, workflow, event):
            return (run_identity(run, workflow, event) and run.get('status') == 'completed' and
                    run.get('conclusion') == 'success')
        observed = bounded_items(api('/commits/' + sha + '/check-runs?filter=latest&per_page=100'), 'check_runs')
        for policy in checks:
            policy.validate()
            require(policy.repository == repo, 'production-release-workflow-policy-invalid')
            matches = [c for c in observed if c.get('name') == policy.name]
            require(matches, 'production-release-required-check-unavailable')
            # Select the trusted push identity before its check. Failed or duplicate
            # same-source push runs cannot be hidden by a successful alternative.
            pushes = [r for r in runs if run_identity(r, policy.workflow, policy.event)]
            require(len(pushes) == 1 and valid_run(pushes[0], policy.workflow, policy.event),
                    'production-release-check-workflow-mismatch')
            push = pushes[0]
            require(type(push.get('check_suite_id')) is int and push['check_suite_id'] > 0,
                    'production-release-check-workflow-mismatch')
            selected = []
            seen_suites = set()
            for check in matches:
                # Validate every same-name claim, including PR checks: filtering by
                # App/SHA first would silently discard contradictory evidence.
                require(check['head_sha'] == sha and type(check['app']['id']) is int and
                        check['app']['id'] == policy.app_id and
                        type(check['check_suite']['id']) is int and check['check_suite']['id'] > 0,
                        'production-release-required-check-untrusted')
                require(check['check_suite']['id'] not in seen_suites,
                        'production-release-required-check-unavailable')
                seen_suites.add(check['check_suite']['id'])
                linked = [r for r in runs if r.get('check_suite_id') == check['check_suite']['id']]
                require(len(linked) == 1, 'production-release-check-workflow-mismatch')
                if check['check_suite']['id'] == push['check_suite_id']:
                    selected.append(check)
                else:
                    # Only independently linked same-repository PR evidence is
                    # irrelevant to the required push result. Never use it as fallback.
                    require(run_identity(linked[0], policy.workflow, 'pull_request'),
                            'production-release-check-workflow-mismatch')
            require(len(selected) == 1, 'production-release-required-check-unavailable')
            require(selected[0]['status'] == 'completed' and selected[0]['conclusion'] == 'success',
                    'production-release-required-check-untrusted')
        publication = None
        if publish is not None:
            publish.validate()
            require(publish.repository == repo and publish.event == 'push',
                    'production-release-workflow-policy-invalid')
            matches = [r for r in runs if valid_run(r, publish.workflow, publish.event)]
            require(len(matches) == 1, 'production-release-publication-unverified')
            run = matches[0]
            references = bounded_items(run['referenced_workflows'])
            require(sum(r.get('path') == repo + '/' + signer_workflow + '@' + signer_sha and
                        r.get('sha') == signer_sha and r.get('ref') == 'refs/heads/main'
                        for r in references) == 1, 'production-release-publication-unverified')
            # Attempt-specific endpoint prevents mixing a prior successful job
            # with a rerun. Never follow API-provided job/check URLs.
            jobs = bounded_items(api('/actions/runs/' + str(run['id']) + '/attempts/' +
                                     str(run['run_attempt']) + '/jobs?per_page=100'), 'jobs')
            jobs = [j for j in jobs if j.get('name') == publish.name]
            require(len(jobs) == 1, 'production-release-publication-unverified')
            job = jobs[0]
            require(type(job['id']) is int and job['id'] > 0 and job['run_id'] == run['id'] and
                    job['run_attempt'] == run['run_attempt'] and job['head_sha'] == sha and
                    job['status'] == 'completed' and job['conclusion'] == 'success',
                    'production-release-publication-unverified')
            linked = [c for c in observed if c.get('id') == job['id'] and c.get('name') == publish.name]
            require(len(linked) == 1, 'production-release-publication-unverified')
            check = linked[0]
            require(check['head_sha'] == sha and check['status'] == 'completed' and
                    check['conclusion'] == 'success' and check['app']['id'] == publish.app_id and
                    type(check['app']['id']) is int and type(run['check_suite_id']) is int and
                    run['check_suite_id'] > 0 and check['check_suite']['id'] == run['check_suite_id'] and
                    job['check_run_url'] == 'https://api.github.com/repos/' + repo + '/check-runs/' + str(check['id']) and
                    check['details_url'] == 'https://github.com/' + repo + '/actions/runs/' +
                    str(run['id']) + '/job/' + str(job['id']), 'production-release-publication-unverified')
            publication = (str(run['id']), str(run['run_attempt']))
        # Close the read round: a moving head or policy prevents authenticated
        # evidence issuance. These API reads are not a historical policy proof.
        require(api('/branches/main') == branch, 'production-release-source-changed')
        self.policy(repo, {(c.name, c.app_id) for c in checks})
        return publication
