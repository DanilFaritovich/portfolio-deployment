"""Bounded OpenSSH transport. No SSH invocation exists in local_check."""

from dataclasses import dataclass
import base64
import hashlib
import struct
import os
from pathlib import Path
import selectors
import subprocess
import time

from scripts.production import InspectionError
from scripts.production.inventory import local_path, secure_file

MAX_OUTPUT = 262144


@dataclass(frozen=True)
class Reply:
    code: int
    stdout: bytes = b''
    stderr: bytes = b''


def run_bounded(argv, *, timeout=20, agent=False):
    # Do not inherit SSH_ASKPASS, proxy variables, shell hooks or Docker settings.
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    if agent and os.environ.get('SSH_AUTH_SOCK'):
        environment['SSH_AUTH_SOCK'] = os.environ['SSH_AUTH_SOCK']
    try:
        with subprocess.Popen(argv, shell=False, env=environment, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            out, err, total = bytearray(), bytearray(), 0
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    selector.register(process.stderr, selectors.EVENT_READ)
                    while selector.get_map():
                        if time.monotonic() >= deadline:
                            raise InspectionError('transport-timeout')
                        for event, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                            chunk = os.read(event.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(event.fileobj)
                                continue
                            total += len(chunk)
                            if total > MAX_OUTPUT:
                                raise InspectionError('transport-output-limit')
                            (out if event.fileobj is process.stdout else err).extend(chunk)
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                return Reply(process.returncode, bytes(out), bytes(err))
            except BaseException:
                process.kill()
                process.wait()
                raise
    except subprocess.TimeoutExpired:
        raise InspectionError('transport-timeout') from None
    except OSError:
        raise InspectionError('transport-unavailable') from None


def local_check(target, known_hosts=None):
    """Only stat, local file reads and ssh-keygen -F (never ssh/ssh-keyscan)."""
    for tool in ('ssh', 'ssh-keygen'):
        if not Path('/usr/bin', tool).is_file():
            raise InspectionError('missing-openssh-client-tools')
    secure_file(target.key, private=True)  # Never read private-key contents.
    trust = local_path(str(known_hosts or Path.home() / '.ssh/known_hosts'))
    secure_file(trust)
    if trust.stat().st_size > MAX_OUTPUT:
        raise InspectionError('known-hosts-output-limit')
    reply = run_bounded(['/usr/bin/ssh-keygen', '-F', target.trust_name, '-f', str(trust)], timeout=5)
    records = [line for line in reply.stdout.splitlines() if line and not line.startswith(b'#')]
    if reply.code not in (0, 1):
        raise InspectionError('cannot-check-host-trust')
    if reply.code or not records:
        raise InspectionError('unknown-host-key: establish independent trust through infrastructure operator procedure')
    if any(line.startswith(b'@') or len(line.split()) < 3 for line in records):
        raise InspectionError('unsupported-or-revoked-host-trust')
    fingerprints(records)  # Reject malformed public host keys locally.
    return trust


def fingerprints(records):
    result = set()
    try:
        for line in records:
            fields = line.split()
            blob = base64.b64decode(fields[2], validate=True)
            length = struct.unpack('>I', blob[:4])[0]
            if blob[4:4+length] != fields[1] or len(blob) < 4+length+8:
                raise ValueError
            result.add('SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
        return sorted(result)
    except (ValueError, IndexError, struct.error):
        raise InspectionError('malformed-public-host-trust') from None


def trust_fingerprints(target, trust):
    reply = run_bounded(['/usr/bin/ssh-keygen', '-F', target.trust_name, '-f', str(trust)], timeout=5)
    if reply.code:
        raise InspectionError('host-trust-changed-during-preflight')
    return fingerprints([line for line in reply.stdout.splitlines() if line and not line.startswith(b'#')])


def ssh_argv(target, trust):
    options = (
        'StrictHostKeyChecking=yes', 'BatchMode=yes', 'IdentitiesOnly=yes',
        'PreferredAuthentications=publickey', 'PubkeyAuthentication=yes',
        'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no', 'GSSAPIAuthentication=no',
        'HostbasedAuthentication=no', 'ForwardAgent=no', 'ForwardX11=no',
        'ClearAllForwardings=yes', 'PermitLocalCommand=no', 'ProxyCommand=none', 'ProxyJump=none',
        'UpdateHostKeys=no', 'VerifyHostKeyDNS=no', 'GlobalKnownHostsFile=/dev/null',
        f'UserKnownHostsFile={trust}', 'ConnectTimeout=8', 'ConnectionAttempts=1',
        'ServerAliveInterval=5', 'ServerAliveCountMax=1', 'ControlMaster=no',
        'ControlPath=none', 'ControlPersist=no', 'RequestTTY=no', 'EscapeChar=none',
        'CanonicalizeHostname=no', 'LogLevel=ERROR', 'KnownHostsCommand=none',
        'CheckHostIP=no', 'AddKeysToAgent=no', 'Tunnel=no',
    )
    return ['/usr/bin/ssh', '-F', '/dev/null', '-n', '-T',
            *[value for option in options for value in ('-o', option)],
            '-i', str(target.key), '-p', str(target.port), '-l', target.user, '--', target.host]


class SSHTransport:
    def __init__(self, target, trust, expected_fingerprints=None):
        self.target, self.trust = target, trust
        self.expected_fingerprints = expected_fingerprints

    def probe(self, name):
        from scripts.production.probes import COMMANDS
        if name not in COMMANDS:
            raise InspectionError('probe-not-allowed')
        # Recheck metadata/trust before every independent connection; no retry.
        local_check(self.target, self.trust)
        if self.expected_fingerprints is not None and trust_fingerprints(self.target, self.trust) != self.expected_fingerprints:
            raise InspectionError('host-key-mismatch')
        reply = run_bounded(ssh_argv(self.target, self.trust) + [COMMANDS[name]], agent=True)
        if reply.code == 255:
            error = reply.stderr.lower()
            if b'host key' in error or b'identification has changed' in error:
                code = 'host-key-mismatch'
            elif b'permission denied' in error:
                code = 'ssh-authentication-failed'
            elif b'timed out' in error:
                code = 'ssh-connection-timeout'
            else:
                code = 'ssh-network-or-transport-failure'
            raise InspectionError(code)
        return reply
