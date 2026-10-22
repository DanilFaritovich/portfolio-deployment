"""Make-facing diagnostics CLI; only inspect can construct an SSH transport."""

import argparse
import os
import sys

from scripts.production import InspectionError
from scripts.production.inventory import load_inventory
from scripts.production.transport import local_check, trust_fingerprints, SSHTransport
from scripts.production.inspection import inspect
from scripts.production.report import load, save, summary, storage_check

DEFAULT_INVENTORY = '../portfolio-server-infrastructure/inventories/production.yml'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'inspect', 'report'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'report':
            report = load()
        else:
            target = load_inventory(os.environ.get('PRODUCTION_INVENTORY', DEFAULT_INVENTORY))
            trust = local_check(target)
            storage_check()
            fingerprints = trust_fingerprints(target, trust)
            print(f'Target: {target.user}@{target.host} port={target.port} alias={target.alias}')
            print('Existing host trust: ' + ', '.join(fingerprints))
            print('Categories: system, Docker resources/ownership, network/gateway, readiness')
            if args.command == 'check':
                print('Local prerequisites and existing trust entry passed; no connection made.\n'
                      'Initial trust provenance, authentication and server readiness remain unverified.')
                return 0
            if not sys.stdin.isatty():
                raise InspectionError('interactive-terminal-required: connection denied')
            if input('Connect for read-only diagnostics? Type INSPECT to confirm [deny]: ').strip() != 'INSPECT':
                raise InspectionError('connection-not-confirmed')
            # Confirmation precedes transport construction and every remote operation.
            report = inspect(target, SSHTransport(target, trust, fingerprints), fingerprints)
            destination = save(report)
            print(f'Local report: {destination}')
        print(summary(report))
        return 2 if report['blockers'] else 0
    except (InspectionError, OSError):
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, InspectionError) else 'Local file operation failed; no repair attempted.', file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print('Inspection cancelled; no retry.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
