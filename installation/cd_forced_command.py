#!/usr/bin/python3
"""Root-owned unprivileged SSH forced command; arguments are fixed by sshd."""
import os
import sys


def main():
    if (len(sys.argv) != 2 or sys.argv[1] not in ('automation', 'observer') or
        os.environ.get('SSH_ORIGINAL_COMMAND') != 'portfolio-cd-v1'):
        return 2
    os.execve('/usr/bin/sudo', ['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-B',
        '/usr/local/lib/portfolio-deployment-cd/cd_launcher.py', sys.argv[1]], {'LANG': 'C', 'PATH': '/usr/bin:/bin'})


if __name__ == '__main__':
    sys.exit(main())
