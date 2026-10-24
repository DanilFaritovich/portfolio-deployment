"""Run ONLY on a reviewed local candidate with CPython 3.12 -I -S -B.

Imports inert runtime modules, exercises native dependencies and package data.
Does not call the installer, launcher, executor or any authority/transport.
"""
import importlib
from pathlib import Path
import platform
import sys

if not (sys.version_info[:2] == (3, 12) and sys.flags.isolated and sys.flags.no_site
        and sys.dont_write_bytecode and platform.machine() == 'x86_64'
        and sys.platform == 'linux'):
    raise RuntimeError('CPython-3.12-linux-amd64-isolated-context-required')

bundle = Path(sys.argv[1]).resolve(strict=True)
sys.path[0:0] = [str(bundle), str(bundle / 'vendor')]
modules = sorted('.'.join(p.relative_to(bundle).with_suffix('').parts)
                 for p in (bundle / 'scripts').rglob('*.py'))
for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).is_relative_to(bundle / 'scripts')

import attrs
import jsonschema
import referencing
import rpds
import typing_extensions
import yaml
from yaml import CLoader

for module in (attrs, jsonschema, referencing, rpds, typing_extensions, yaml):
    assert Path(module.__file__).is_relative_to(bundle / 'vendor')
assert yaml.load('a: 1', Loader=CLoader) == {'a': 1}
assert rpds.HashTrieMap({'a': 1})['a'] == 1
jsonschema.Draft202012Validator.check_schema({'type': 'object'})
jsonschema.validate({'a': 1}, {'type': 'object'})

from scripts.production_integration.caddy_release import load_lock
assert load_lock()['version'] == '2.11.7'
print(f'Imported {len(modules)} runtime modules; native wheels and schema data passed')
print(f'Interpreter: {sys.version.split()[0]}; libc: {platform.libc_ver()}')
