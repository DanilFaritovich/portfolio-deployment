"""Offline failure boundaries for workstation wheel export, without downloads."""
import io
import json
import stat
import zipfile

import pytest

from installation.operator_bootstrap import InstallError
from scripts import prepare_installation_vendor as vendor


def wheel(tmp_path, *, change=None):
    record = 'referencing-0.37.0.dist-info/RECORD'
    files = {
        'referencing/__init__.py': b'# package\n',
        'referencing/tests/test_core.py': b'# omitted\n',
        'referencing/data/schema.json': b'{}',
        'referencing-0.37.0.dist-info/licenses/LICENSE': b'license',
        'referencing-0.37.0.dist-info/METADATA':
            b'Name: referencing\nVersion: 0.37.0\nRequires-Python: >=3.10\n\n',
    }
    files[record] = vendor.record_bytes(files, record)
    if change:
        change(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for n, raw in files.items():
            entry = zipfile.ZipInfo(n)
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, raw)
    raw = output.getvalue()
    path = tmp_path / 'referencing-0.37.0-py3-none-any.whl'
    path.write_bytes(raw)
    return path, dict(name='referencing', version='0.37.0', sha256=vendor.sha(raw),
                      requires_python='>=3.10', requires_dist=[])


def test_export_keeps_runtime_data_and_licenses_regenerating_record(tmp_path):
    path, entry = wheel(tmp_path)
    first, omitted = vendor.wheel_files(path, entry)
    second, _ = vendor.wheel_files(path, entry)
    assert first == second
    assert omitted == ['referencing/tests/test_core.py']
    assert first['referencing/data/schema.json'] == b'{}'
    assert first['referencing-0.37.0.dist-info/licenses/LICENSE'] == b'license'
    record = first.pop('referencing-0.37.0.dist-info/RECORD')
    assert record == vendor.record_bytes(first, 'referencing-0.37.0.dist-info/RECORD')
    assert b'tests/' not in record


@pytest.mark.parametrize('case', ['outer-hash', 'record-hash', 'extra-file', 'traversal', 'symlink',
                                 'abi', 'pth', 'bytecode'])
def test_invalid_wheels_rejected_before_export(tmp_path, case):
    def change(files):
        if case == 'record-hash':
            files['referencing/__init__.py'] = b'tampered'
        if case == 'extra-file':
            files['unlisted.py'] = b'unlisted'
        if case == 'traversal':
            files['../outside'] = b'unsafe'
        if case in ('pth', 'bytecode'):
            record = 'referencing-0.37.0.dist-info/RECORD'
            files['hook.pth' if case == 'pth' else 'module.pyo'] = b'forbidden'
            files.pop(record)
            files[record] = vendor.record_bytes(files, record)
    path, entry = wheel(tmp_path, change=change)
    if case == 'outer-hash':
        entry['sha256'] = '0'*64
    if case == 'symlink':
        linked = tmp_path / 'link.whl'
        linked.symlink_to(path)
        path = linked
    if case == 'abi':
        new = tmp_path / 'referencing-0.37.0-cp314-cp314-manylinux2014_x86_64.whl'
        path.rename(new)
        path = new
    with pytest.raises((ValueError, InstallError)):
        vendor.wheel_files(path, entry)


def test_python312_marker_cannot_omit_or_underpin_typing_extensions(tmp_path, monkeypatch):
    lock = json.loads(vendor.LOCK.read_bytes())
    output = tmp_path / 'vendor'
    # A native-Python-3.14 resolver would omit this dependency entirely. The
    # target marker must also reject an insufficient explicitly pinned version.
    next(e for e in lock['wheels'] if e['name'] == 'typing-extensions')['version'] = '4.3.0'
    monkeypatch.setattr(vendor, 'wheel_files', lambda *args: ({}, []))
    with pytest.raises(ValueError, match='runtime-dependency-unsatisfied'):
        vendor.prepare(tmp_path, output, lock)
    assert not output.exists()
    lock['wheels'] = [e for e in lock['wheels'] if e['name'] != 'typing-extensions']
    with pytest.raises(ValueError, match='runtime-closure-invalid'):
        vendor.prepare(tmp_path, output, lock)
    assert not output.exists()


def test_export_will_not_adopt_existing_destination(tmp_path):
    keep = tmp_path / 'keep'
    keep.write_bytes(b'preserve')
    with pytest.raises(ValueError, match='vendor-output-must-be-absent'):
        vendor.prepare(tmp_path, tmp_path, json.loads(vendor.LOCK.read_bytes()))
    assert keep.read_bytes() == b'preserve'
