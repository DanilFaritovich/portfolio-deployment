"""Read-only App/API integration on protected develop; no image operations."""
import argparse
import os
import re

from scripts.caddy_producer import REPOSITORY, source_failure_code, verify_main_source
from scripts.production_integration.release_transport import GitHubRead, HTTPSRead, bounded_items, require


def verify():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and
            os.environ.get('GITHUB_EVENT_NAME') == 'push' and
            os.environ.get('GITHUB_REF') == 'refs/heads/develop' and
            os.environ.get('GITHUB_REF_PROTECTED') == 'true', 'caddy-protected-develop-required')
    factory = lambda: GitHubRead(os.environ.get('GH_TOKEN'), HTTPSRead())
    github = factory()
    branch = github.get(REPOSITORY, '/branches/develop')
    require(branch['name'] == 'develop' and branch['protected'] is True and
            branch['commit']['sha'] == os.environ.get('GITHUB_SHA'), 'caddy-protected-develop-required')
    sha = github.get(REPOSITORY, '/branches/main')['commit']['sha']
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha) is not None,
            'production-release-api-incomplete')
    verify_main_source(factory, sha)


def require_success():
    """Credential-free PR code uses only its read-only Actions token, never App secrets.

    Required Offline checks on a release PR cannot pass until the identical
    protected develop revision has passed the live App check after review/merge.
    """
    sha = os.environ.get('EXPECTED_DEVELOP_SHA', '')
    require(re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'caddy-source-smoke-required')
    github = GitHubRead(os.environ.get('GH_TOKEN'), HTTPSRead())
    branch = github.get(REPOSITORY, '/branches/develop')
    require(branch['protected'] is True and branch['commit']['sha'] == sha, 'caddy-source-smoke-required')
    runs = bounded_items(github.get(REPOSITORY,
        '/actions/workflows/caddy-source-smoke.yml/runs?head_sha=' + sha + '&event=push&per_page=100'), 'workflow_runs')
    require(len(runs) == 1, 'caddy-source-smoke-required')
    run = runs[0]
    require(run['head_sha'] == sha and run['head_branch'] == 'develop' and run['event'] == 'push' and
            run['path'] == '.github/workflows/caddy-source-smoke.yml' and
            run['repository']['full_name'] == run['head_repository']['full_name'] == REPOSITORY and
            run['status'] == 'completed' and run['conclusion'] == 'success', 'caddy-source-smoke-required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('verify', 'require-success'))
    args = parser.parse_args()
    try:
        (verify if args.operation == 'verify' else require_success)()
    except Exception as error:
        # No traceback/response body even for an unexpected dependency failure.
        code = source_failure_code(error)
        print(str(error) if str(error) == 'caddy-source-smoke-required' else code)
        return 1
    print('caddy-source-smoke-passed' if args.operation == 'verify' else 'caddy-source-smoke-confirmed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
