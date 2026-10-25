"""Automation SSHSIG delegation is confined to reviewed main workflow runs."""
import base64
import re
import time

from scripts.cd.policy import pin
from scripts.cd.release import source_file
from scripts.cd.signatures import authenticate, REQUEST_NAMESPACE
from scripts.production_integration.observations import require
from scripts.production_integration.release import MANAGER, PRODUCER
from scripts.production_integration.release_source import ProtectedSource, WorkflowCheck, PRODUCTION_REVIEW_POLICY


def verify_context(raw, envelope, candidate, *, policy, github, kind):
    policy.current()
    role = policy.document['automation']
    payload, _ = authenticate(raw, keys={role['principal']: role['key']}, namespace=REQUEST_NAMESPACE)
    require(type(payload) is dict and set(payload) == {'approval_sha256', 'candidate', 'repository', 'source_sha',
                'run_id', 'run_attempt', 'not_before', 'expires'}, 'cd-context-fields-invalid')
    repository = MANAGER if kind == 'manager' else PRODUCER
    require(payload['repository'] == repository and payload['approval_sha256'] == pin(envelope) and
            payload['candidate'] == candidate.identity and re.fullmatch(r'[0-9a-f]{40}', payload['source_sha']) and
            all(type(payload[k]) is int and payload[k] > 0 for k in ('run_id', 'run_attempt')) and
            all(type(payload[k]) is int for k in ('not_before', 'expires')) and
            payload['not_before'] <= time.time() < payload['expires'] and
            0 < payload['expires'] - payload['not_before'] <= 300, 'cd-context-binding-invalid')
    p = policy.document['deployment_workflows'][repository]
    require(pin(source_file(github, repository, p['path'], payload['source_sha'])) == p['sha256'],
            'cd-deployment-workflow-unreviewed')
    run = github.get(repository, '/actions/runs/' + str(payload['run_id']))
    require(run['id'] == payload['run_id'] and run['run_attempt'] == payload['run_attempt'] and
            run['path'] == p['path'] and run['event'] == 'workflow_run' and run['head_branch'] == 'main' and
            run['head_sha'] == payload['source_sha'] and run['repository']['full_name'] == repository and
            run['head_repository']['full_name'] == repository and run['status'] == 'in_progress',
            'cd-deployment-run-untrusted')
    checks = policy.document['manager' if kind == 'manager' else 'application']['checks']
    ProtectedSource(github, review_policy=PRODUCTION_REVIEW_POLICY).collect(repository, payload['source_sha'],
        [WorkflowCheck(**v) for v in checks])
    return payload
