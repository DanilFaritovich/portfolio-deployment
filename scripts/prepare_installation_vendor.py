"""Offline CPython 3.12 vendor export from the reviewed C4.4 binary wheel lock.

Workstation tooling only; no pip execution, source builds, downloads or approvals.
Keep package data/licenses. Omit bundled tests/benchmarks, and regenerate RECORD.
"""
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename

from installation.operator_bootstrap import safe_name

LOCK = Path(__file__).resolve().parents[1] / 'artifacts/c4.4/wheels.json'
ENVIRONMENT = {
    'implementation_name': 'cpython', 'implementation_version': '3.12.3',
    'os_name': 'posix', 'platform_machine': 'x86_64', 'platform_release': '',
    'platform_system': 'Linux', 'platform_version': '',
    'python_full_version': '3.12.3', 'platform_python_implementation': 'CPython',
    'python_version': '3.12', 'sys_platform': 'linux', 'extra': '',
}
PACKAGES = frozenset({'pyyaml', 'jsonschema', 'attrs', 'jsonschema-specifications',
                      'referencing', 'rpds-py', 'typing-extensions'})


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record_bytes(files, record):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    for name, raw in sorted(files.items()):
        pin = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + pin, len(raw)))
    writer.writerow((record, '', ''))
    return output.getvalue().encode()


def wheel_files(path, entry):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 16*1024*1024,
            'wheel-file-invalid')
    raw = path.read_bytes()
    require(sha(raw) == entry['sha256'], 'wheel-sha256-mismatch')
    name, version, _, tags = parse_wheel_filename(path.name)
    require(name == canonicalize_name(entry['name']) and str(version) == entry['version'] and
            any((t.interpreter == 'py3' and t.abi == 'none' and t.platform == 'any') or
                (t.interpreter == 'cp312' and t.abi == 'cp312' and
                 t.platform in ('manylinux2014_x86_64', 'manylinux_2_17_x86_64')) for t in tags),
            'wheel-target-invalid')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 1024 and len({e.filename for e in entries}) == len(entries) and
                sum(e.file_size for e in entries) <= 32*1024*1024, 'wheel-bounds-invalid')
        files = {}
        for e in entries:
            p = PurePosixPath(e.filename)
            require(not p.is_absolute() and '..' not in p.parts and
                    stat.S_IFMT(e.external_attr >> 16) in (0, stat.S_IFDIR, stat.S_IFREG) and
                    not e.flag_bits & 1, 'wheel-path-or-type-invalid')
            if e.is_dir():
                continue
            require(str(p) == e.filename and not any(x.endswith('.data') for x in p.parts),
                    'wheel-path-or-layout-invalid')
            files[e.filename] = archive.read(e)
    records = [n for n in files if n.endswith('.dist-info/RECORD')]
    require(len(records) == 1, 'wheel-record-missing')
    record = records[0]
    rows = list(csv.reader(io.StringIO(files[record].decode())))
    require(all(len(r) == 3 for r in rows) and len({r[0] for r in rows}) == len(rows) and
            {r[0] for r in rows} == set(files), 'wheel-record-incomplete')
    for n, pin, size in rows:
        if n == record:
            require(pin == size == '', 'wheel-record-self-invalid')
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(files[n]).digest()).rstrip(b'=').decode()
            require(pin == 'sha256=' + expected and size == str(len(files[n])), 'wheel-record-mismatch')
    metadata = BytesParser().parsebytes(files[record.removesuffix('RECORD') + 'METADATA'])
    require(canonicalize_name(metadata['Name']) == name and metadata['Version'] == str(version) and
            metadata.get('Requires-Python') == entry['requires_python'] and
            sorted(str(Requirement(r)) for r in metadata.get_all('Requires-Dist', [])) ==
            sorted(str(Requirement(r)) for r in entry['requires_dist']) and
            SpecifierSet(metadata['Requires-Python']).contains('3.12.3'), 'wheel-metadata-mismatch')
    omitted = sorted(n for n in files if {'tests', 'benchmarks'} & set(PurePosixPath(n).parts))
    files = {n: r for n, r in files.items() if n not in omitted and n != record}
    for n in files:
        safe_name(n)
        require(not n.endswith(('.pyo', '.pyc', '.pth')), 'vendor-hook-or-bytecode-invalid')
    files[record] = record_bytes(files, record)
    return files, omitted


def prepare(wheelhouse, output, lock):
    require(not output.exists() and not output.is_symlink(), 'vendor-output-must-be-absent')
    require(wheelhouse.is_dir() and not wheelhouse.is_symlink(), 'wheelhouse-invalid')
    entries = lock['wheels']
    versions = {canonicalize_name(e['name']): e['version'] for e in entries}
    require(len(versions) == len(entries) and set(versions) == PACKAGES, 'runtime-closure-invalid')
    files, omitted = {}, {}
    for e in entries:
        require(Path(e['filename']).name == e['filename'], 'wheel-filename-invalid')
        for text in e['requires_dist']:
            r = Requirement(text)
            if r.marker is None or r.marker.evaluate(ENVIRONMENT):
                dependency = canonicalize_name(r.name)
                require(not r.url and not r.extras and dependency in versions and
                        r.specifier.contains(versions[dependency]), 'runtime-dependency-unsatisfied')
        selected, excluded = wheel_files(wheelhouse / e['filename'], e)
        require(not set(selected) & set(files), 'wheel-file-collision')
        files.update(selected)
        omitted[e['filename']] = excluded
    require(len(files) <= 2048 and sum(map(len, files.values())) <= 32*1024*1024, 'vendor-bounds-invalid')
    output.mkdir(mode=0o700)
    for n, raw in sorted(files.items()):
        target = output / n
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as stream:
            stream.write(raw)
        target.chmod(0o600)
    return {'productionTrusted': False, 'omitted': omitted,
            'files': {'vendor/' + n: sha(raw) for n, raw in sorted(files.items())}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = prepare(args.wheelhouse, args.output, json.loads(LOCK.read_bytes()))
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
