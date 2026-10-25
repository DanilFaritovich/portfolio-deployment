"""Protected manual Actions orchestration. Never signs operator approvals.

Keep the legacy bundle source selection unchanged: older verifier capsules must
independently recognize the exact same Git-tree closure during Manager upgrades.
This module is inert in an installed bundle and has no host-side entry point.
"""
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

from scripts.cd.runner import Client, require_policy_id, report_failure
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import closed_json


def dispatch(client, action, scope, revision, directory, approval=''):
    require(re.fullmatch('[0-9a-f]{40}', revision), 'bootstrap-exact-manager-sha-required')
    if action == 'install':
        require(not approval, 'bootstrap-unexpected-approval')
        from installation.operator_bootstrap import LIMIT
        with (Path(directory) / 'portfolio-deployment.zip').open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        require(0 < len(raw) <= LIMIT, 'bootstrap-bundle-size-invalid')
        result = client.call({'op': 'bootstrap-install', 'manager_sha': revision, 'length': len(raw)}, raw=raw)
        require(result.get('status') == 'installed' and result.get('manager_sha') == revision and
                result.get('executorStarted') is False, 'bootstrap-installation-failed')
        return result
    require(action in ('prepare', 'execute'), 'bootstrap-action-invalid')
    request = {'op': 'bootstrap-' + action, 'scope': scope, 'manager_sha': revision}
    if action == 'execute':
        # Candidate and original operator envelope travel together. Repreparing
        # a different candidate must never silently retarget an existing approval.
        document = closed_json(base64.b64decode(approval, validate=True), 32768)
        require(set(document) == {'candidate', 'envelope'}, 'bootstrap-approval-fields-invalid')
        request.update(document)
    else:
        require(not approval, 'bootstrap-unexpected-approval')
    result = client.call(request)
    require(result.get('status') == ('prepared' if action == 'prepare' else 'succeeded'),
            'bootstrap-operation-failed')
    return result


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
            os.environ.get('GITHUB_REPOSITORY') == 'DanilFaritovich/portfolio-deployment' and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('REF_PROTECTED') == 'true',
            'bootstrap-protected-main-required')
    from scripts.caddy_producer import wait_for_offline
    from scripts.production_integration.release_transport import GitHubRead, HTTPSRead
    revision = os.environ['BOOTSTRAP_MANAGER_SHA']
    require(re.fullmatch('[0-9a-f]{40}', revision), 'bootstrap-exact-manager-sha-required')
    wait_for_offline(lambda: GitHubRead(os.environ['GH_TOKEN'], HTTPSRead()), revision)
    action, scope = os.environ['BOOTSTRAP_ACTION'], os.environ['BOOTSTRAP_SCOPE']
    observe = action == 'execute' and scope == 'first-project-router-rollout'
    if observe:
        require_policy_id()
    with tempfile.TemporaryDirectory(prefix='pd-bootstrap-') as temporary:
        client = Client(temporary, roles=('automation', 'observer') if observe else ('automation',))
        stop = threading.Event()
        thread = threading.Thread(target=client.observer, args=(stop,), daemon=True) if observe else None
        if thread:
            thread.start()
        try:
            result = dispatch(client, action, scope, os.environ['BOOTSTRAP_MANAGER_SHA'], sys.argv[1],
                              os.environ.get('BOOTSTRAP_APPROVAL_B64', ''))
        finally:
            stop.set()
            if thread:
                thread.join(timeout=30)
        output = Path(sys.argv[1])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'bootstrap-result.json').write_text(json.dumps(result))
        print(json.dumps(result))


def entrypoint():
    try:
        main()
        return 0
    except Exception as error:
        report_failure(error, operation='production-bootstrap')
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
