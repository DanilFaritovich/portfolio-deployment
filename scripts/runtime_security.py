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


def matches(host, wanted):
    """Engine inspect normalized against generated settings, including legacy absence."""
    if bool(host.get('ReadonlyRootfs')) != wanted.get('read_only', False):
        return False
    if sorted(host.get('CapDrop') or []) != sorted(wanted.get('cap_drop', [])):
        return False
    opts = [x.replace('no-new-privileges:true', 'no-new-privileges') for x in host.get('SecurityOpt') or []]
    expected = ['no-new-privileges'] if wanted.get('security_opt') else []
    if opts != expected:
        return False
    restart = host.get('RestartPolicy') or {}
    if (restart.get('Name') or 'no') != wanted.get('restart', 'no') or restart.get('MaximumRetryCount', 0) != 0:
        return False
    expected_tmpfs = dict(t.split(':', 1) for t in wanted.get('tmpfs', []))
    actual_tmpfs = host.get('Tmpfs') or {}
    if set(actual_tmpfs) != set(expected_tmpfs):
        return False
    return all(set(actual_tmpfs[k].split(',')) == set(v.split(',')) for k, v in expected_tmpfs.items())
