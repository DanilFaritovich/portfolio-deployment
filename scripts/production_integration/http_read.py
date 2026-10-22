"""Host-local HTTP readiness; numeric reviewed endpoints only, no proxies/DNS."""
import errno
import ipaddress
import math
import re
import selectors
import socket
import time

from scripts.docker_adapter import DockerError


def exchange_status(ip, port, path, timeout):
    """Numeric IPv4, absolute deadline and 4KiB status-line cap, no body reads."""
    deadline = time.monotonic() + timeout
    request = f'GET {path} HTTP/1.1\r\nHost: {ip}:{port}\r\nConnection: close\r\n\r\n'.encode('ascii')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, selectors.DefaultSelector() as selector:
        sock.setblocking(False)
        code = sock.connect_ex((ip, port))
        if code not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
            raise OSError
        selector.register(sock, selectors.EVENT_WRITE)
        sent, received = 0, bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise OSError
            if sent < len(request):
                if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                    raise OSError
                try:
                    count = sock.send(request[sent:])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise OSError
                sent += count
                if sent == len(request):
                    selector.modify(sock, selectors.EVENT_READ)
            else:
                try:
                    chunk = sock.recv(min(1024, 4096 - len(received)))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError
                received.extend(chunk)
                if b'\r\n' in received:
                    match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e]*)?', received.split(b'\r\n', 1)[0])
                    if not match:
                        raise OSError
                    return int(match[1])
                if len(received) >= 4096:
                    raise OSError


class InternalHTTPRead:
    def __init__(self, subnet, endpoints, *, exchange=exchange_status):
        self.subnet = ipaddress.ip_network(subnet, strict=True)
        if self.subnet.version != 4 or not self.subnet.is_private:
            raise DockerError('production-http-private-subnet-required')
        self.endpoints = frozenset(endpoints)  # admitted (port, path), never a URL
        if not self.endpoints or len(self.endpoints) > 64:
            raise DockerError('production-http-contract-invalid')
        for port, path in self.endpoints:
            if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(path, str) or
                not re.fullmatch(r'/[A-Za-z0-9/_-]{0,255}', path)):
                raise DockerError('production-http-contract-invalid')
        self.exchange = exchange

    def get(self, ip, port, path, *, timeout, follow_redirects=False):
        try:
            address = ipaddress.ip_address(ip)
            if (str(address) != ip or address not in self.subnet or
                address in (self.subnet.network_address, self.subnet.broadcast_address) or
                (port, path) not in self.endpoints or follow_redirects is not False or
                type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10):
                raise ValueError
        except (ValueError, TypeError):
            raise DockerError('production-http-endpoint-not-approved') from None
        try:
            return self.exchange(ip, port, path, timeout)
        except OSError:
            raise DockerError('production-http-read-unavailable') from None
