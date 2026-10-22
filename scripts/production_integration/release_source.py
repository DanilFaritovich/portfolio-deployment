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

    def collect(self, repo, sha, checks, publish=None):
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
        def valid_run(run, workflow, event):
            return (type(run.get('id')) is int and run['id'] > 0 and
                    type(run.get('run_attempt')) is int and run['run_attempt'] > 0 and
                    run.get('head_sha') == sha and run.get('head_branch') == 'main' and
                    run.get('path') == workflow and run.get('event') == event and
                    run.get('status') == 'completed' and run.get('conclusion') == 'success' and
                    run.get('repository', {}).get('id') == metadata['id'] and
                    run.get('repository', {}).get('full_name') == repo and
                    run.get('head_repository', {}).get('id') == metadata['id'] and
                    run.get('head_repository', {}).get('full_name') == repo)
        observed = bounded_items(api('/commits/' + sha + '/check-runs?filter=latest&per_page=100'), 'check_runs')
        for policy in checks:
            matches = [c for c in observed if c.get('name') == policy.name]
            require(len(matches) == 1, 'production-release-required-check-unavailable')
            check = matches[0]
            require(check['head_sha'] == sha and check['status'] == 'completed' and
                    check['conclusion'] == 'success' and type(check['app']['id']) is int and
                    check['app']['id'] == policy.app_id and
                    type(check['check_suite']['id']) is int and check['check_suite']['id'] > 0,
                    'production-release-required-check-untrusted')
            linked = [r for r in runs if r.get('check_suite_id') == check['check_suite']['id'] and
                      valid_run(r, policy.workflow, policy.event)]
            require(len(linked) == 1, 'production-release-check-workflow-mismatch')
        publication = None
        if publish is not None:
            matches = [r for r in runs if valid_run(r, publish, 'push')]
            require(len(matches) == 1, 'production-release-publication-unverified')
            publication = (str(matches[0]['id']), str(matches[0]['run_attempt']))
        # Close the read round: a moving head or policy prevents authenticated
        # evidence issuance. These API reads are not a historical policy proof.
        require(api('/branches/main') == branch, 'production-release-source-changed')
        self.policy(repo, {(c.name, c.app_id) for c in checks})
        return publication
