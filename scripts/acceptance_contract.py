"""Offline acceptance declarations; expected claims never establish source trust."""
from pathlib import Path
import re
import shutil

from scripts.compose import render_compose
from scripts.reconciliation import encode
from scripts.planning_inputs import build_desired_state
from scripts.validate_registry import load_document, read_bounded, validate_registry


class AcceptanceError(Exception):
    """No untrusted diagnostics enter acceptance reports."""


class AcceptanceBlocked(AcceptanceError):
    """A required capability or reviewed trust contract is unavailable."""


def require(condition):
    if not condition:
        raise AcceptanceError()


def candidate_model(root, directory, application, revision, session):
    """Load one reviewed declaration; reuse complete registry/admission validation."""
    require(re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', application or '') is not None)
    require(len(application) <= 32)
    # Validate the real registry too; candidate cannot shadow an active identity.
    registry = validate_registry(root)
    paths = [root / folder / (application + '.yaml') for folder in ('docs/candidates', 'registry/applications')]
    selected = [p for p in paths if p.exists() or p.is_symlink()]
    if len(selected) != 1:
        raise AcceptanceBlocked()
    for parent in selected[0].parents:
        if parent == root:
            break
        require(parent.is_dir() and not parent.is_symlink())
    raw = read_bounded(selected[0])
    (directory / 'registry/applications').mkdir(parents=True)
    (directory / 'policy').mkdir()
    shutil.copyfile(root / 'policy/admission.yaml', directory / 'policy/admission.yaml')
    for document in registry.applications:
        filename = document['metadata']['id'] + '.yaml'
        (directory / 'registry/applications' / filename).write_bytes(
            read_bounded(root / 'registry/applications' / filename))
    (directory / 'registry/applications' / (application + '.yaml')).write_bytes(raw)
    desired = build_desired_state(directory, revision)
    app = next(a for a in desired.applications if a.id == application)
    if selected[0].parent == root / 'docs/candidates':
        require(app.spec['enabled'] is False)
    document = load_document(raw)
    policies = load_document(read_bounded(root / 'policy/admission.yaml'))['applications']
    policy = next(p for p in policies if p['id'] == application)
    release = document.get('release')
    trust = policy.get('acceptance')
    if not release or not trust:
        raise AcceptanceBlocked()
    components = {c['name']: c for c in app.spec['components']}
    require(set(release['components']) == set(components) == set(trust['runtimeSecurity']))
    for name, component in components.items():
        identity = release['components'][name]
        require(identity['signerWorkflow'] in trust['signerWorkflows'])
        require(identity['sourceRef'] in trust['sourceRefs'])
        predicates = [a['predicateType'] for a in identity['attestations']]
        require(len(predicates) == len(set(predicates)))
        require(set(trust['requiredPredicates']) <= set(predicates))
        require('https://slsa.dev/provenance/v1' in predicates)
        constraints = trust.get('predicateClaims', [])
        types = [c['predicateType'] for c in constraints]
        require(len(types) == len(set(types)))
        for constraint in constraints:
            require(constraint['predicateType'] in trust['requiredPredicates'])
            declaration = next(a for a in identity['attestations'] if a['predicateType'] == constraint['predicateType'])
            claims = declaration.get('expectedPredicate', {})
            # Policy pins required top-level JSON claims; release may add exact
            # run/source-specific expectations. Nothing interprets predicate keys.
            require(all(key in claims and encode(claims[key]) == encode(value)
                        for key, value in constraint['expectedPredicate'].items()))
        require(component.get('runtimeSecurity') == trust['runtimeSecurity'][name])
        require(component.get('runtimeSecurity', {}).get('readOnlyRootFilesystem') is True)
        if (component['configRefs'] or component['secretRefs'] or app.spec['configRefs'] or app.spec['secretRefs']):
            raise AcceptanceBlocked()
    if app.spec['storage']['type'] == 'persistent' and release.get('volumePolicy') != 'remove-isolated':
        raise AcceptanceBlocked()
    return app, release, render_compose(app, test_session=session)


def main():
    """Offline validation of the bounded inactive candidate set, including policy."""
    import tempfile
    root = Path(__file__).resolve().parents[1]
    try:
        paths = sorted((root / 'docs/candidates').iterdir())
        require(len(paths) <= 128)
        for path in paths:
            require(path.suffix == '.yaml' and not path.is_symlink())
            with tempfile.TemporaryDirectory(prefix='pd-candidate-') as temporary:
                candidate_model(root, Path(temporary), path.stem, '0' * 40, '0' * 16)
    except Exception:
        print('Acceptance declarations: invalid or blocked')
        return 1
    print(f'Acceptance declarations: {len(paths)} valid (offline; no artifact/runtime verification)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
