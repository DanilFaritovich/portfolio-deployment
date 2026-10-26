"""Bounded GET-only AF_UNIX capability for the fixed Caddy ACTIVE endpoint.

No URL, method, body, headers, socket path or generic Admin API is exposed.
The separately granted reload capability uses the same protected endpoint.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time

from scripts.docker_adapter import DockerError
from scripts.production_integration.admin_contract import HOST_DIRECTORY, SOCKET_NAME
from scripts.production_integration.observations import require

LIMIT = 1024 * 1024
HEADER_LIMIT = 16384
REQUEST = b'GET /config/ HTTP/1.1\r\nHost: localhost\r\nAccept: application/json\r\nConnection: close\r\n\r\n'


def metadata(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)


def socket_metadata(info):
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
            'production-admin-socket-metadata-invalid')
    return metadata(info)


@contextmanager
def protected_socket():
    """Walk NOFOLLOW dirfds and connect through the held directory descriptor.

    Root trust is explicit. Neither a writable ancestor nor a symlink is allowed.
    Rewalk after I/O detects directory and socket replacement at the fixed path.
    """
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root = os.fstat(fd)
        require(root.st_uid == root.st_gid == 0 and not root.st_mode & 0o022, 'production-admin-ancestry-unsafe')
        ancestry = [metadata(root)]
        for part in Path(HOST_DIRECTORY).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022,
                    'production-admin-ancestry-unsafe')
            ancestry.append(metadata(info))
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, 'production-admin-directory-mode-invalid')
        info = os.stat(SOCKET_NAME, dir_fd=fd, follow_symlinks=False)
        token = (tuple(ancestry), socket_metadata(info))
        yield '/proc/self/fd/' + str(fd) + '/' + SOCKET_NAME, token
        require(socket_metadata(os.stat(SOCKET_NAME, dir_fd=fd, follow_symlinks=False)) == token[1],
                'production-admin-socket-replaced')
    except OSError:
        raise DockerError('production-admin-socket-unavailable') from None
    finally:
        if fd is not None:
            os.close(fd)


def socket_identity():
    with protected_socket() as (_, token):
        return token


def http_body(raw, limit):
    """Strict bounded HTTP/1.1 framing, including Go's chunked JSON responses."""
    header, separator, body = raw.partition(b'\r\n\r\n')
    require(separator and len(header) <= HEADER_LIMIT, 'production-admin-http-invalid')
    lines = header.split(b'\r\n')
    require(re.fullmatch(rb'HTTP/1\.1 200 [\x20-\x7e]*', lines[0]), 'production-admin-http-status')
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b':')
        require(sep and re.fullmatch(rb'[A-Za-z0-9-]+', name) and
                not re.search(rb'[^\x20-\x7e\t]', value) and name.lower() not in headers,
                'production-admin-http-invalid')
        headers[name.lower()] = value.strip()
    require(headers.get(b'content-type') == b'application/json' and
            b'content-encoding' not in headers and b'location' not in headers,
            'production-admin-http-invalid')
    length, transfer = headers.get(b'content-length'), headers.get(b'transfer-encoding')
    require((length is None) != (transfer is None), 'production-admin-http-framing')
    if length is not None:
        require(re.fullmatch(rb'[0-9]{1,8}', length) and int(length) == len(body) and
                0 < len(body) <= limit, 'production-admin-http-length')
        return body
    require(transfer == b'chunked', 'production-admin-http-framing')
    decoded = bytearray()
    chunks = 0
    while True:
        size, sep, body = body.partition(b'\r\n')
        require(sep and re.fullmatch(rb'[0-9a-fA-F]{1,8}', size), 'production-admin-http-chunk')
        count = int(size, 16)
        if count == 0:
            require(body == b'\r\n' and decoded, 'production-admin-http-chunk')
            return bytes(decoded)
        chunks += 1
        require(chunks <= 4096 and len(decoded) + count <= limit and
                len(body) >= count + 2 and body[count:count+2] == b'\r\n', 'production-admin-http-chunk')
        decoded.extend(body[:count])
        body = body[count+2:]


def _read_socket(address, expected_pid, *, timeout, limit, clock=time.monotonic):
    """Private HTTP engine shared by the disposable acceptance, not an API port."""
    deadline = clock() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        def remaining():
            left = deadline - clock()
            require(left > 0, 'production-admin-read-timeout')
            connection.settimeout(left)
        remaining()
        connection.connect(address)
        pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require((pid, uid, gid) == (expected_pid, 0, 0), 'production-admin-peer-invalid')
        remaining()
        connection.sendall(REQUEST)
        raw = bytearray()
        while True:
            remaining()
            chunk = connection.recv(min(65536, LIMIT + 65536 + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= limit + 65536, 'production-admin-response-limit')
            header = raw.find(b'\r\n\r\n')
            require(header <= HEADER_LIMIT if header >= 0 else len(raw) <= HEADER_LIMIT,
                    'production-admin-header-limit')
    return http_body(bytes(raw), limit)


class UnixActiveChannel:
    def __init__(self, runtime, *, clock=time.monotonic):
        self.runtime, self.clock = runtime, clock
        self._snapshot = None

    @contextmanager
    def observation(self, snapshot):
        """Share one authenticated snapshot across two GETs and outer recheck."""
        window = getattr(self.runtime, '_read_window', None)
        require(self._snapshot is None and window is not None and window.active and
                self.runtime.inspect(self) == snapshot, 'production-admin-observation-unbound')
        self._snapshot = snapshot
        try:
            yield
        finally:
            self._snapshot = None

    def read_active(self, container_id, *, timeout, limit):
        require(type(timeout) in (int, float) and 0 < timeout <= 1 and
                type(limit) is int and 0 < limit <= LIMIT, 'production-admin-read-bound-invalid')
        before = self._snapshot if self._snapshot is not None else self.runtime.inspect(self)
        require(container_id == before.container_id, 'production-admin-container-changed')
        deadline = self.clock() + timeout
        try:
            with protected_socket() as (address, token):
                require(token == before.channel_identity[0], 'production-admin-socket-replaced')
                body = _read_socket(address, before.channel_identity[1],
                                    timeout=deadline - self.clock(), limit=limit, clock=self.clock)
            require(self.clock() <= deadline, 'production-admin-read-timeout')
            require(socket_identity() == token, 'production-admin-runtime-changed')
            if self._snapshot is None:
                require(before == self.runtime.inspect(self), 'production-admin-runtime-changed')
            else:
                self.runtime._read_window.verify()
            return body
        except (OSError, ValueError):
            raise DockerError('production-admin-read-unavailable') from None
