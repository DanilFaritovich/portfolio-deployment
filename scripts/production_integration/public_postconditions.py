"""Fixed ProjectRouter public probe; bounded DNS and pinned-address HTTPS.

No ambient resolver, proxies, redirects, URL input or implicit CA directories.
Trusted bindings supply the independently approved policy and external witness.
"""
from dataclasses import asdict, dataclass
import hashlib
import ipaddress
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import time

from scripts.docker_adapter import DockerError
from scripts.gateway import GatewayRoute
from scripts.production_integration.candidate import hashed
from scripts.production_integration.observations import require

HOST = 'danil-miftakhov.dev'
ORIGIN = 'https://' + HOST
PATH = '/en/'
BODY_LIMIT = 256 * 1024
HEADER_LIMIT = 16384
TOTAL_TIMEOUT = 15


@dataclass(frozen=True)
class PublicPolicy:
    identity: tuple[str, str]
    ipv4: tuple[str, ...]
    ipv6: tuple[str, ...]  # empty explicitly forbids AAAA, never skips the query
    resolver: str         # independently approved numeric DNS resolver
    ca_file: str         # reviewed regular PEM file, not ambient SSL_CERT_*
    ca_sha256: str
    body_sha256: str     # exact independently reviewed ProjectRouter /en/ bytes

    def validate(self):
        require(type(self.identity) is tuple and len(self.identity) == 2 and
                re.fullmatch(r'[0-9a-f]{32}', self.identity[0]) and
                re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', self.identity[1]), 'public-policy-identity-invalid')
        require(type(self.ipv4) is tuple and type(self.ipv6) is tuple and
                0 < len(self.ipv4) + len(self.ipv6) <= 4, 'public-policy-addresses-invalid')
        try:
            ipaddress.ip_address(self.resolver)
            for version, addresses in ((4, self.ipv4), (6, self.ipv6)):
                require(len(set(addresses)) == len(addresses), 'public-policy-addresses-invalid')
                for address in addresses:
                    parsed = ipaddress.ip_address(address)
                    require(parsed.version == version and parsed.is_global and str(parsed) == address,
                            'public-policy-addresses-invalid')
        except (ValueError, TypeError):
            raise DockerError('public-policy-addresses-invalid') from None
        require(type(self.ca_file) is str and self.ca_file.startswith('/') and
                all(type(v) is str and re.fullmatch(r'[0-9a-f]{64}', v)
                    for v in (self.ca_sha256, self.body_sha256)), 'public-policy-trust-invalid')


def remaining(deadline, clock=time.monotonic):
    value = deadline - clock()
    require(value > 0, 'public-probe-timeout')
    return min(2, value)


def tls_context(ca_pem):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=ca_pem.decode('ascii'))
    return context


class PublicNetwork:
    """Real transport; at most two UDP queries and four direct TLS requests.

    DNS aliases/truncation/unknown answers fail closed. No getaddrinfo, NSS or
    fallback; TLS independently authenticates the fixed hostname. A forged DNS
    reply cannot add an unapproved destination or weaken certificate validation.
    """
    def resolve(self, resolver, qtype, deadline):
        transaction = secrets.token_bytes(2)
        question = b''.join(bytes([len(p)]) + p.encode('ascii') for p in HOST.split('.')) + b'\0' + struct.pack('!HH', qtype, 1)
        query = transaction + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0) + question
        family = socket.AF_INET6 if ':' in resolver else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as channel:
            channel.settimeout(remaining(deadline))
            channel.connect((resolver, 53))
            channel.send(query)
            raw = channel.recv(4097)
        require(12 <= len(raw) <= 4096 and raw[:2] == transaction, 'public-dns-invalid')
        flags, questions, answers, authority, additional = struct.unpack('!HHHHH', raw[2:12])
        require(flags & 0xFA0F == 0x8000 and questions == 1 and answers <= 4 and
                authority + additional <= 32 and raw[12:12 + len(question)] == question,
                'public-dns-invalid')
        offset = 12 + len(question)
        addresses = []
        # Only the fixed question name, directly or via a pointer to offset 12,
        # may own an answer. CNAME and other answer types are intentionally closed.
        for _ in range(answers):
            name = raw[offset:offset + 2]
            if name == b'\xc0\x0c':
                offset += 2
            else:
                encoded = question[:-4]
                require(raw[offset:offset + len(encoded)] == encoded, 'public-dns-alias-rejected')
                offset += len(encoded)
            require(offset + 10 <= len(raw), 'public-dns-invalid')
            kind, cls, ttl, size = struct.unpack('!HHIH', raw[offset:offset + 10])
            offset += 10
            require(kind == qtype and cls == 1 and ttl > 0 and size == (4 if qtype == 1 else 16) and
                    offset + size <= len(raw), 'public-dns-answer-rejected')
            addresses.append(str(ipaddress.ip_address(raw[offset:offset + size])))
            offset += size
        require(len(set(addresses)) == len(addresses), 'public-dns-invalid')
        return tuple(sorted(addresses))

    def https(self, address, ca_pem, deadline):
        context = tls_context(ca_pem)
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as channel:
            channel.settimeout(remaining(deadline))
            channel.connect((address, 443))
            with context.wrap_socket(channel, server_hostname=HOST, do_handshake_on_connect=False) as tls:
                tls.settimeout(remaining(deadline))
                tls.do_handshake()
                require(str(ipaddress.ip_address(tls.getpeername()[0])) == address, 'public-peer-mismatch')
                tls.settimeout(remaining(deadline))
                tls.sendall(('GET /en/ HTTP/1.1\r\nHost: ' + HOST + '\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n').encode('ascii'))
                raw = bytearray()
                while True:
                    tls.settimeout(remaining(deadline))
                    chunk = tls.recv(8192)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    require(len(raw) <= HEADER_LIMIT + BODY_LIMIT, 'public-http-size-limit')
                    split = raw.find(b'\r\n\r\n')
                    require(split <= HEADER_LIMIT and (split >= 0 or len(raw) <= HEADER_LIMIT), 'public-http-header-limit')
        return bytes(raw)


def accepted_body(raw, expected):
    require(type(raw) is bytes and len(raw) <= HEADER_LIMIT + BODY_LIMIT, 'public-http-size-limit')
    head, separator, body = raw.partition(b'\r\n\r\n')
    require(separator and len(head) <= HEADER_LIMIT and 0 < len(body) <= BODY_LIMIT, 'public-http-invalid')
    lines = head.split(b'\r\n')
    require(re.fullmatch(rb'HTTP/1\.[01] 200 [\x20-\x7e]*', lines[0]), 'public-http-status-rejected')
    headers = {}
    for line in lines[1:]:
        key, colon, value = line.partition(b':')
        require(colon and re.fullmatch(rb'[A-Za-z0-9-]+', key) and key.lower() not in headers and
                all(32 <= c <= 126 or c == 9 for c in value), 'public-http-headers-rejected')
        headers[key.lower()] = value.strip().lower()
    require(b'location' not in headers and b'transfer-encoding' not in headers and
            headers.get(b'content-encoding', b'identity') == b'identity' and
            headers.get(b'content-type', b'').split(b';')[0] == b'text/html' and
            headers.get(b'content-length') == str(len(body)).encode(), 'public-http-headers-rejected')
    require(hashlib.sha256(body).hexdigest() == expected, 'public-project-router-content-mismatch')


class PublicPostconditions:
    def __init__(self, policy, *, external, network=None, clock=time.monotonic):
        require(type(policy) is PublicPolicy, 'public-policy-required')
        policy.validate()
        self.policy, self.external = policy, external
        self.network, self.clock = PublicNetwork() if network is None else network, clock

    def probe(self, routes, identity):
        """Network observation only; external signer may call this off VDS."""
        p = self.policy
        p.validate()
        require(identity == p.identity, 'public-runtime-identity-mismatch')
        require(type(routes) is tuple and len(routes) == 1 and type(routes[0]) is GatewayRoute and
                routes[0].key == ('project-router', 'web', HOST, 8080, 'project-router-web') and
                routes[0].git_revision is not None, 'public-route-contract-mismatch')
        deadline = self.clock() + TOTAL_TIMEOUT
        try:
            with Path(p.ca_file).open('rb') as stream:
                ca = stream.read(1024 * 1024 + 1)
            require(0 < len(ca) <= 1024 * 1024 and hashlib.sha256(ca).hexdigest() == p.ca_sha256,
                    'public-ca-pin-mismatch')
            for kind, expected in ((1, p.ipv4), (28, p.ipv6)):
                actual = self.network.resolve(p.resolver, kind, deadline)
                require(actual == tuple(sorted(expected)), 'public-dns-address-policy-mismatch')
            for address in p.ipv4 + p.ipv6:
                accepted_body(self.network.https(address, ca, deadline), p.body_sha256)
            require(self.clock() < deadline, 'public-probe-timeout')
        except DockerError:
            raise
        except ssl.SSLError:
            raise DockerError('public-tls-verification-failed') from None
        except (OSError, ValueError, UnicodeError, struct.error):
            raise DockerError('public-network-unavailable') from None
        return {'origin': ORIGIN, 'path': PATH, 'identity': list(identity),
                'routes': [asdict(r) for r in routes], 'policy': hashed(asdict(p))}

    def verify(self, routes, identity):
        start = self.clock()
        subject = self.probe(routes, identity)
        require(self.external is not None, 'public-independent-external-observer-unconfigured')
        budget = TOTAL_TIMEOUT - (self.clock() - start)
        require(budget > 0, 'public-probe-timeout')
        require(self.external.verify(subject, timeout=budget) == subject,
                'public-independent-external-proof-invalid')
        require(self.clock() - start < TOTAL_TIMEOUT, 'public-probe-timeout')
        return routes
