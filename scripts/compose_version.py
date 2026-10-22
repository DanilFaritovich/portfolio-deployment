"""Shared Compose CLI support policy; version acceptance is not runtime evidence."""
import re


def supported_compose(value):
    # Version syntax is an eligibility gate, never capability/runtime evidence.
    if not isinstance(value, str) or len(value) > 80:
        return False
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[A-Za-z0-9]+(?:[.~+-][A-Za-z0-9]+)*)?', value)
    return bool(match and int(match[1]) >= 2)


# Only read-only plugin commands. Never fall back to the legacy docker-compose.
CLI_REQUIREMENTS = (
    (("compose", "--help"), ("--project-name", "--project-directory", "--env-file", "--file")),
    (("compose", "up", "--help"), ("--detach", "--no-deps", "--no-build", "--pull")),
    (("compose", "config", "--help"), ("--quiet",)),
)
HELP_SEPARATOR = '\nPD_COMPOSE_HELP\n'


def supported_compose_cli(outputs):
    """Require actual option declarations, not mentions elsewhere in help text."""
    if len(outputs) != len(CLI_REQUIREMENTS):
        return False
    declarations = []
    for output, (_, flags) in zip(outputs, CLI_REQUIREMENTS):
        if not isinstance(output, str) or len(output) > 262144:
            return False
        matches = list(re.finditer(r'^[ \t]*(?:-[A-Za-z],[ \t]*)?(--[a-z][a-z-]*)(?=[\s=,]|$)', output, re.M))
        options = {match[1]: output[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(output)]
                   for index, match in enumerate(matches)}
        if not set(flags).issubset(options):
            return False
        declarations.append(options)
    # The enum may wrap across lines in terminal-width-dependent plugin help.
    return bool(re.search(r'\bnever\b', declarations[1]['--pull']))
