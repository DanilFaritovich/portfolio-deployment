"""Version eligibility and required plugin features are independent offline gates."""
import pytest

from scripts.compose_version import CLI_REQUIREMENTS, HELP_SEPARATOR, supported_compose, supported_compose_cli
from scripts.production.probes import normalize
from scripts.production import InspectionError

# Representative plugin help, independent of the policy's required-option list.
HELP = (
    'Options:\n  -p, --project-name string\n      --project-directory string\n      --env-file stringArray\n  -f, --file stringArray\n',
    'Options:\n  -d, --detach\n      --no-deps\n      --no-build\n      --pull string   Pull image before running ("always"|"missing"|"never")\n',
    'Options:\n  -q, --quiet   Only validate the configuration\n',
)


def cli_reply(args):
    for index, (command, _) in enumerate(CLI_REQUIREMENTS):
        if list(command) == args:
            return HELP[index].encode()
    raise AssertionError(args)


@pytest.mark.parametrize('version', ['2.0.0', 'v2.40.3', '5.6.0', 'v5.6.1', '5.7.0', '5.99.123',
                                     '6.0.0', 'v6.3.2', '10.0.0', '2.40.3+ds1-0ubuntu1~24.04.1'])
def test_stable_modern_plugin(version):
    assert supported_compose(version)
    assert normalize('compose', version.encode()) == {'value': version}


@pytest.mark.parametrize('version', [None, 6, b'6.0.0', '', '1.29.2', '0.0.0', '5.6', 'v05.6.0',
                                     '5.06.0', '6.0.01', '6.0.0-rc1', '2.40.0-beta+build',
                                     '5.6.0\nanything', ' 6.0.0', 'Docker Compose version v6.0.0',
                                     '6.0.0+', '6.0.0+..', '6.0.0+build.', '9'*81])
def test_invalid_or_legacy_plugin(version):
    assert not supported_compose(version)


def test_cli_inspection_is_separate_from_version():
    assert supported_compose_cli(HELP)
    raw = (HELP_SEPARATOR + HELP_SEPARATOR.join(HELP)).encode()
    assert normalize('compose_cli', raw) == {'supported': True}
    with pytest.raises(InspectionError):
        normalize('compose_cli', raw.replace(b'--no-build', b'--no-cache'))


@pytest.mark.parametrize('index,flag', [(index, flag) for index, (_, flags) in enumerate(CLI_REQUIREMENTS)
                                      for flag in flags])
def test_every_required_option_fails_closed(index, flag):
    broken = list(HELP)
    broken[index] = broken[index].replace(flag, '--unsupported')
    assert not supported_compose_cli(broken)
    broken[index] += '\nExample mentions ' + flag
    assert not supported_compose_cli(broken)


def test_pull_never_and_complete_help_required():
    assert not supported_compose_cli(HELP[:2])
    assert not supported_compose_cli((HELP[0], HELP[1].replace('never', 'sometimes'), HELP[2]))
    assert supported_compose_cli((HELP[0], HELP[1].replace('("always"', '\n          ("always"'), HELP[2]))
