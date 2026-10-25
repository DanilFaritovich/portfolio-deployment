"""Independent bundle review AND exact-byte GitHub artifact provenance."""
from scripts.cd.policy import pin
from scripts.cd.release import source_file
from scripts.production_integration.bundle_review import ManagerBundleVerifier
from scripts.production_integration.observations import require
from scripts.production_integration.release import MANAGER
from scripts.production_integration.release_transport import bounded_items


class ManagerReleaseVerifier:
    def __init__(self, policy, github, crypto):
        self.policy, self.github, self.crypto = policy, github, crypto

    def verify(self, raw):
        from scripts.production_integration.release_source import WorkflowCheck
        p = self.policy.current().document['manager']
        verifier = ManagerBundleVerifier(github=self.github, checks=[WorkflowCheck(**c) for c in p['checks']],
                                        asset_pins=tuple(p['assets'].items()), bindings_pin=p['frozen']['bindings.py'])
        proof = verifier.verify(raw)
        require(pin(source_file(self.github, MANAGER, p['workflow'], proof.manager_sha)) == p['workflow_sha256'],
                'cd-manager-workflow-change-requires-review')
        runs = bounded_items(self.github.get(MANAGER, '/actions/runs?head_sha=' + proof.manager_sha + '&per_page=100'), 'workflow_runs')
        matches = [r for r in runs if r.get('path') == p['workflow'] and r.get('event') == 'push']
        require(len(matches) == 1, 'cd-manager-release-run-ambiguous')
        run = matches[0]
        require(run['head_sha'] == proof.manager_sha and run['head_branch'] == 'main' and
                run['repository']['full_name'] == MANAGER and run['head_repository']['full_name'] == MANAGER and
                type(run['id']) is int and run['id'] > 0 and type(run['run_attempt']) is int and run['run_attempt'] > 0 and
                run['status'] == 'completed' and run['conclusion'] == 'success', 'cd-manager-release-not-successful')
        digest = 'sha256:' + pin(raw)
        documents = self.github.get(MANAGER, '/attestations/' + digest + '?per_page=100')['attestations']
        require(type(documents) is list and 0 < len(documents) <= 10, 'cd-manager-attestation-missing')
        identity = {'signerWorkflow': p['workflow'], 'signerSha': proof.manager_sha,
                    'sourceSha': proof.manager_sha, 'sourceRef': 'refs/heads/main',
                    'signerIdentity': 'https://github.com/' + MANAGER + '/' + p['workflow'] + '@refs/heads/main'}
        statements = self.crypto.verify(raw, p['bundle_name'] + '@' + digest, identity,
            ({'predicateType': 'https://slsa.dev/provenance/v1', 'expectedInvocation':
              'https://github.com/' + MANAGER + '/actions/runs/' + str(run['id']) + '/attempts/' + str(run['run_attempt'])},),
            [d['bundle'] for d in documents])
        require(type(statements) is tuple and len(statements) == 1 and
                statements[0].get('predicateType') == 'https://slsa.dev/provenance/v1' and
                statements[0].get('subject') == [{'name': p['bundle_name'], 'digest': {'sha256': pin(raw)}}],
                'cd-manager-signed-subject-invalid')
        return proof
