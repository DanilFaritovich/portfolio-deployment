"""Narrow first-install transport; authority remains in independent admin intake."""
from pathlib import Path
import subprocess

from installation.operator_bootstrap import LIMIT, read_bundle, exclusive
from scripts.cd.policy import pin
from scripts.production_integration.installation import protected
from scripts.production_integration.observations import require
from scripts.production_integration.operator_approval import closed_json

BASE = Path('/var/lib/portfolio-deployment-bootstrap')
ROOT = Path('/opt/portfolio-deployment')
HELPER = Path('/usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py')
CAPSULE = Path('/usr/local/lib/portfolio-deployment-cd')
SCOPES = ('first-bootstrap', 'prepare-project-router-image', 'first-project-router-rollout')


def checked_process(argv, *, raw=None, timeout=1500):
    result = subprocess.run(argv, input=raw, capture_output=True, env={'LANG': 'C'},
                            cwd='/', timeout=timeout, check=False)
    require(0 < len(result.stdout) <= 131072, 'bootstrap-result-unavailable')
    from scripts.planning_inputs import parse_snapshot
    document = parse_snapshot(result.stdout.strip())
    require(result.returncode == 0 and type(document) is dict, 'bootstrap-operation-failed')
    return document


def install(request, stream):
    require(set(request) == {'op', 'length', 'manager_sha'} and type(request['length']) is int and
            0 < request['length'] <= LIMIT, 'bootstrap-install-fields-invalid')
    # This path can never replace, adopt or repair an existing Manager.
    require(not ROOT.exists() and not ROOT.is_symlink(), 'bootstrap-manager-already-installed')
    raw = stream.read(request['length'] + 1)
    require(len(raw) == request['length'], 'bootstrap-bundle-framing-invalid')
    manifest, _ = read_bundle(raw)
    require(manifest['manager_sha'] == request['manager_sha'], 'bootstrap-manager-sha-mismatch')
    protected(BASE, directory=True)
    protected(HELPER)
    decision = BASE / 'installation-decision.json'
    protected(decision)
    require(decision.stat().st_size <= 2 * 1024 * 1024, 'bootstrap-decision-too-large')
    document = closed_json(decision.read_bytes(), 2 * 1024 * 1024)
    require(document['bundle_sha256'] == pin(raw) and document['manager_sha'] == request['manager_sha'],
            'bootstrap-independent-installation-review-required')
    # Reuse full independent source/tree/dependency AND GitHub provenance checks
    # before even publishing intake bytes. Initial trust is pinned in the capsule
    # inventory because no installed Manager inventory exists yet.
    from scripts.cd.bridge import manager_verifier
    from scripts.cd.policy import read_policy
    from scripts.production_integration.operator_approval import TRUST, trust_keys
    protected(TRUST)
    require(TRUST.stat().st_size <= 8192, 'bootstrap-operator-trust-too-large')
    inventory = closed_json((CAPSULE / 'capsule.json').read_bytes(), 2 * 1024 * 1024)['files']
    require(inventory.get(str(TRUST)) == pin(TRUST.read_bytes()), 'bootstrap-operator-trust-unpinned')
    policy = read_policy(trust_keys(TRUST.read_bytes()))
    require(policy.document['initial_manager_sha'] == request['manager_sha'], 'bootstrap-initial-manager-unreviewed')
    proof = manager_verifier(policy).verify(raw)
    require(proof.manager_sha == request['manager_sha'] and proof.bundle_sha256 == pin(raw),
            'bootstrap-release-unbound')
    target = BASE / 'bundle.zip'
    if target.exists() or target.is_symlink():
        protected(target)
        require(target.stat().st_size <= LIMIT and target.read_bytes() == raw, 'bootstrap-intake-conflict')
    else:
        exclusive(target, raw)
    # The independently installed stdlib helper reauthenticates and consumes the
    # exact one-use admin decision BEFORE any installation writes. No bundled code.
    return checked_process(['/usr/bin/python3', '-I', '-S', '-B', str(HELPER)])


def operation(request):
    require(request.get('op') in ('bootstrap-prepare', 'bootstrap-execute'), 'bootstrap-operation-forbidden')
    fields = {'op', 'scope', 'manager_sha'}
    if request['op'] == 'bootstrap-execute':
        fields |= {'candidate', 'envelope'}
    require(set(request) == fields and request['scope'] in SCOPES, 'bootstrap-operation-fields-invalid')
    from scripts.production_integration.operator_approval import canonical
    return checked_process(['/usr/bin/python3', '-I', '-S', '-B', str(CAPSULE / 'bootstrap_entry.py')],
                           raw=canonical(request))
