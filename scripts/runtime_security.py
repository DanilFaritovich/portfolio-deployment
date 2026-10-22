"""Closed per-component runtime security v1; no arbitrary Compose escape hatch."""
import json
from pathlib import Path

from scripts.reconciliation import PlanningInputError
from scripts.validate_registry import StrictValidator


def projection(contract):
    schema = json.loads((Path(__file__).resolve().parents[1] / 'schemas/runtime-security-v1.schema.json').read_text())
    if not StrictValidator(schema).is_valid(contract):
        raise PlanningInputError('unsupported-runtime-security')
    return {'read_only': contract['readOnlyRootFilesystem'], 'user': contract['user'],
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'restart': contract['restart'],
            **({'tmpfs': [f"/tmp:rw,noexec,nosuid,nodev,size={t['sizeBytes']},mode=1777" for t in contract['tmpfs']]}
               if contract['tmpfs'] else {})}


def _options(value):
    if type(value) is not str:
        raise ValueError('invalid-tmpfs')
    parts = value.split(',')
    names = [p.split('=', 1)[0] for p in parts]
    if len(set(names)) != len(names) or not all(parts):
        raise ValueError('invalid-tmpfs')
    return frozenset(parts)


def differing_fields(host, wanted):
    """Fixed names only; this owns both diagnostics and pass/fail decisions."""
    fields = []
    actual = host.get('ReadonlyRootfs', False)
    if type(actual) is not bool or actual != wanted.get('read_only', False):
        fields.append('ReadonlyRootfs')
    caps = host.get('CapDrop')
    caps = [] if caps is None else caps
    if type(caps) is not list or any(type(c) is not str for c in caps) or sorted(caps) != sorted(wanted.get('cap_drop', [])):
        fields.append('CapDrop')
    opts = host.get('SecurityOpt')
    opts = [] if opts is None else opts
    expected = ['no-new-privileges'] if wanted.get('security_opt') else []
    if (type(opts) is not list or any(type(o) is not str for o in opts) or
        [o if o != 'no-new-privileges:true' else 'no-new-privileges' for o in opts] != expected):
        fields.append('SecurityOpt')
    restart = host.get('RestartPolicy')
    restart = {} if restart is None else restart
    if (type(restart) is not dict or restart.keys() - {'Name', 'MaximumRetryCount'} or
        (restart.get('Name', 'no') or 'no') != wanted.get('restart', 'no') or
        type(restart.get('MaximumRetryCount', 0)) is not int or restart.get('MaximumRetryCount', 0) != 0):
        fields.append('RestartPolicy')
    expected_tmpfs = dict(t.split(':', 1) for t in wanted.get('tmpfs', []))
    actual_tmpfs = host.get('Tmpfs')
    actual_tmpfs = {} if actual_tmpfs is None else actual_tmpfs
    try:
        valid = (type(actual_tmpfs) is dict and set(actual_tmpfs) == set(expected_tmpfs) and
                 all(_options(actual_tmpfs[k]) == _options(v) for k, v in expected_tmpfs.items()))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        fields.append('Tmpfs')
    return tuple(fields)


def matches(host, wanted):
    """Legacy boolean interface uses exactly the structured field checker."""
    return not differing_fields(host, wanted)
