"""Synthetic transcripts only: no host contact, private reports or authority."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts.production import host_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]


def record(path, *, kind='regular file', owner='0:0', mode='755', links=1):
    return f'{path} {kind} {owner} {mode} 1:2 {links}\n{path}\n' + 'a' * 64 + f'  {path}\n'


def transcript():
    return ('3.12.3 (main, date) [GCC 13.3.0]\n/usr/bin/python3\n'
            'cpython-312-x86_64-linux-gnu\n3.45.1\n/usr/bin/python3.12\n'
            'Docker version 29.9.0, build f415da8\n5.6.0\n' +
            ''.join(record(p) for p in evidence.TOOLS.values()) +
            ''.join('MISSING ' + p + '\n' for p in evidence.PLUGINS[:-1]) +
            'gh version 2.102.0 (2026-09-30)\n').encode()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network attempted'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('command attempted'))


def tool(result, name):
    return next(t for t in result['tools'] if t['tool'] == name)


def test_observations_never_become_authority_or_approved_pins():
    result = evidence.assess(transcript())
    assert all(t['status'] == 'OBSERVED' for t in result['tools'])
    assert result['versions'] == {'python': '3.12.3', 'abi': 'cpython-312-x86_64-linux-gnu',
                                 'docker': '29.9.0', 'compose': '5.6.0', 'gh': '2.102.0'}
    assert result['singleFixedComposePlugin'] == 'OBSERVED'
    assert result['installation'] == 'BLOCKED' and result['productionAuthorized'] is False
    assert result['protectedToolAncestry'] == result['dependencyProvenance'] == 'UNKNOWN'
    assert result['runtimeCompatibility'] == 'UNKNOWN'
    assert 'a' * 64 not in json.dumps(result)
    assert 'VERIFIED' not in json.dumps(result)


def test_actual_preflight_shape_has_no_resolved_python_metadata():
    raw = transcript().replace(record(evidence.TOOLS['python']).encode(), b'')
    raw += record('/usr/bin/python3', kind='symbolic link').encode()
    assert tool(evidence.assess(raw), 'python')['status'] == 'UNKNOWN'


@pytest.mark.parametrize('options', [dict(kind='symbolic link'), dict(kind='directory'),
    dict(owner='1000:0'), dict(owner='0:1000'), dict(mode='775'), dict(mode='757'),
    dict(mode='644'), dict(links=2)])
def test_unsafe_tool_metadata(options):
    p = evidence.TOOLS['docker']
    raw = transcript().replace(record(p).encode(), record(p, **options).encode())
    assert tool(evidence.assess(raw), 'docker')['status'] == 'BLOCKED'


@pytest.mark.parametrize('replacement,status', [(b'', 'UNKNOWN'),
    (b'MISSING /usr/bin/docker\n', 'BLOCKED'),
    (b'/usr/bin/docker regular file 0:0 755 1:2 1\n', 'UNKNOWN')])
def test_missing_or_partial_record_never_establishes_absence(replacement, status):
    raw = transcript().replace(record(evidence.TOOLS['docker']).encode(), replacement)
    assert tool(evidence.assess(raw), 'docker')['status'] == status


def test_hash_must_immediately_bind_same_resolved_path():
    raw = transcript().replace(b'a' * 64 + b'  /usr/bin/docker', b'a' * 64 + b'  /usr/bin/gh')
    assert tool(evidence.assess(raw), 'docker')['status'] == 'UNKNOWN'
    raw = transcript().replace(b'/usr/bin/docker\n' + b'a' * 64, b'/different\n' + b'a' * 64)
    assert tool(evidence.assess(raw), 'docker')['status'] == 'UNKNOWN'


def test_duplicate_and_conflicting_records_block():
    assert tool(evidence.assess(transcript() + record('/usr/bin/docker').encode()), 'docker')['status'] == 'BLOCKED'
    assert tool(evidence.assess(transcript() + b'MISSING /usr/bin/docker\n'), 'docker')['status'] == 'BLOCKED'


@pytest.mark.parametrize('extra,status', [(record(evidence.PLUGINS[0]), 'BLOCKED'),
    ('MISSING ' + evidence.PLUGINS[-1] + '\n', 'BLOCKED')])
def test_plugin_conflicts(extra, status):
    assert evidence.assess(transcript() + extra.encode())['singleFixedComposePlugin'] == status


def test_missing_plugin_directory_evidence_is_unknown():
    raw = transcript().replace(('MISSING ' + evidence.PLUGINS[0] + '\n').encode(), b'permission denied\n')
    assert evidence.assess(raw)['singleFixedComposePlugin'] == 'UNKNOWN'


def test_wrong_or_ambiguous_gh_version():
    assert evidence.assess(transcript().replace(b'2.102.0', b'2.101.0'))['requiredGhVersion'] == 'BLOCKED'
    assert evidence.assess(transcript() + b'gh version 2.102.0 (2026-09-30)\n')['requiredGhVersion'] == 'UNKNOWN'


def test_private_text_and_identifiers_do_not_escape(capsys, tmp_path):
    private = b'PRIVATE-SECRET machine-id daemon-id 192.0.2.90 ssh-ed25519 key\n'
    path = tmp_path / 'private-report'
    path.write_bytes(transcript() + private + b'not-json {\n')
    assert evidence.main(['--report', str(path)]) == 0
    output = capsys.readouterr().out
    assert all(v not in output for v in ('PRIVATE', '192.0.2.90', 'machine-id', str(path), 'a' * 64))
    assert len(output) < 4096


@pytest.mark.parametrize('raw', [b'\xff', b'x' * (evidence.MAX_BYTES + 1), b'\n' * (evidence.MAX_LINES + 1)])
def test_input_bounds(raw):
    with pytest.raises(evidence.EvidenceError):
        evidence.assess(raw)


def test_empty_transcript_has_no_observations():
    assert all(t['status'] == 'UNKNOWN' for t in evidence.assess(b'')['tools'])


def test_regular_file_intake_only(tmp_path, capsys):
    report = tmp_path / 'report'
    report.write_bytes(transcript())
    assert evidence.read_report(report) == transcript()
    link = tmp_path / 'symlink'
    link.symlink_to(report)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    for path in (link, fifo, tmp_path, tmp_path / 'absent'):
        assert evidence.main(['--report', str(path)]) == 2
        assert str(path) not in capsys.readouterr().out


def test_make_wrapper_is_offline_and_handles_missing_input(tmp_path, monkeypatch):
    # The wrapper needs a subprocess; unlike the checker it may launch Python.
    monkeypatch.undo()
    report = tmp_path / 'private-report'
    report.write_bytes(transcript())
    env = dict(os.environ, HOST_EVIDENCE_REPORT=str(report))
    result = subprocess.run(['make', 'production-host-evidence', f'PYTHON={sys.executable}'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)['productionAuthorized'] is False
    env.pop('HOST_EVIDENCE_REPORT')
    result = subprocess.run(['make', 'production-host-evidence', f'PYTHON={sys.executable}'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert json.loads(result.stdout)['error'] == 'host-evidence-input-unavailable'


def test_adversarial_versions_cannot_expand_output():
    raw = transcript().replace(b'29.9.0', b'9' * 20000 + b'.1.0')
    raw = raw.replace(b'2.102.0', b'9' * 20000 + b'.1.0')
    result = evidence.assess(raw)
    assert result['versions']['docker'] is None
    assert result['versions']['gh'] is None
    assert len(json.dumps(result)) < 4096
