"""Offline public-route checks; no real resolver, network or Engine access."""
from dataclasses import replace
import hashlib
from pathlib import Path
import socket
import ssl
import struct
from types import SimpleNamespace

import pytest

from scripts.docker_adapter import DockerError
from scripts.gateway import GatewayRoute
from scripts.production_integration import public_postconditions as public
from scripts.production_integration.install_bundle import source_target
from scripts.production_integration.runtime_sources import GatewaySource


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('live network attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def setup(tmp_path):
    ca = tmp_path / 'ca.pem'
    ca.write_bytes(b'reviewed-ca')
    body = b'<!doctype html><title>ProjectRouter</title>'
    policy = public.PublicPolicy(('a'*32, 'daemon'), ('8.8.8.8',), (), '1.1.1.1', str(ca),
                                hashlib.sha256(ca.read_bytes()).hexdigest(), hashlib.sha256(body).hexdigest())
    raw = b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    calls = []
    state = SimpleNamespace(raw=raw, v4=policy.ipv4, v6=(), now=1, error=None, proof=True)
    class Network:
        def resolve(self, resolver, kind, deadline):
            calls.append(('dns', resolver, kind))
            if state.error:
                raise state.error
            return state.v4 if kind == 1 else state.v6
        def https(self, address, ca, deadline):
            calls.append(('https', address))
            return state.raw
    class Witness:
        def verify(self, subject, *, timeout):
            assert 0 < timeout <= public.TOTAL_TIMEOUT
            calls.append(('external', subject))
            return subject if state.proof else None
    port = public.PublicPostconditions(policy, external=Witness(), network=Network(), clock=lambda: state.now)
    routes = (GatewayRoute('project-router', 'web', public.HOST, 8080, 'project-router-web', git_revision='b'*40),)
    return port, routes, state, calls


def test_gateway_source_uses_public_check_and_preserves_exact_payload(setup):
    port, routes, _, calls = setup
    engine = SimpleNamespace(identity=lambda: port.policy.identity)
    source = GatewaySource(engine, SimpleNamespace(read=lambda: routes), postconditions=port, clock=lambda: 1)
    assert source.collect('round').payload is routes
    assert calls[:3] == [('dns', '1.1.1.1', 1), ('dns', '1.1.1.1', 28), ('https', '8.8.8.8')]
    subject = calls[-1][1]
    assert subject['routes'][0]['git_revision'] == 'b'*40
    assert subject['identity'] == ['a'*32, 'daemon']
    assert subject['origin'] == 'https://danil-miftakhov.dev'


@pytest.mark.parametrize('case', ['identity', 'hostname', 'port', 'revision', 'empty', 'extra', 'dns4', 'dns6',
    'mixed', 'tls', 'dns-timeout', 'status', 'redirect', 'body', 'huge', 'chunked', 'duplicate', 'encoding',
    'ca', 'external', 'proof', 'deadline'])
def test_fail_closed(setup, case):
    port, routes, state, calls = setup
    identity = port.policy.identity
    if case == 'identity': identity = ('c'*32, 'daemon')
    elif case == 'hostname': routes = (replace(routes[0], hostname='example.org'),)
    elif case == 'port': routes = (replace(routes[0], port=80),)
    elif case == 'revision': routes = (replace(routes[0], git_revision=None),)
    elif case == 'empty': routes = ()
    elif case == 'extra': routes = routes + routes
    elif case == 'dns4': state.v4 = ()
    elif case == 'dns6': state.v6 = ('2606:4700:4700::1111',)
    elif case == 'mixed': state.v4 += ('127.0.0.1',)
    elif case == 'tls': state.error = ssl.SSLCertVerificationError('untrusted')
    elif case == 'dns-timeout': state.error = TimeoutError()
    elif case == 'status': state.raw = state.raw.replace(b'200 OK', b'503 Unavailable')
    elif case == 'redirect': state.raw = state.raw.replace(b'Content-Type:', b'Location: https://evil.example/\r\nContent-Type:')
    elif case == 'body': state.raw = state.raw[:-1] + b'x'
    elif case == 'huge': state.raw += b'x' * public.BODY_LIMIT
    elif case == 'chunked': state.raw = state.raw.replace(b'Content-Type:', b'Transfer-Encoding: chunked\r\nContent-Type:')
    elif case == 'duplicate': state.raw = state.raw.replace(b'Content-Type:', b'Content-Length: 1\r\nContent-Type:')
    elif case == 'encoding': state.raw = state.raw.replace(b'Content-Type:', b'Content-Encoding: gzip\r\nContent-Type:')
    elif case == 'ca': Path(port.policy.ca_file).write_bytes(b'changed')
    elif case == 'external': port.external = None
    elif case == 'proof': state.proof = False
    else:
        port.clock = iter([1, 1, 100]).__next__
    with pytest.raises(DockerError):
        port.verify(routes, identity)
    if case in ('identity', 'hostname', 'port', 'revision', 'empty', 'extra', 'ca'):
        assert not calls


def test_dual_stack_checks_every_address(setup):
    port, routes, state, calls = setup
    port.policy = replace(port.policy, ipv6=('2606:4700:4700::1111',))
    state.v6 = port.policy.ipv6
    assert port.verify(routes, port.policy.identity) is routes
    assert ('https', '2606:4700:4700::1111') in calls


@pytest.mark.parametrize('change', [dict(ipv4=('127.0.0.1',)), dict(ipv4=()), dict(ipv6=('8.8.8.8',)),
    dict(ipv4=('8.8.8.8',)*2), dict(resolver='request.example'), dict(ca_sha256='bad')])
def test_policy_rejects_unknown_or_private_destinations(setup, change):
    port, _, _, _ = setup
    with pytest.raises(DockerError):
        replace(port.policy, **change).validate()


@pytest.mark.parametrize('case', ['valid', 'mismatch', 'truncated', 'nxdomain', 'alias', 'wrong-type', 'oversize'])
def test_real_dns_wire_parser_is_bounded(monkeypatch, case):
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): assert 0 < value <= 2
        def connect(self, endpoint): assert endpoint == ('1.1.1.1', 53)
        def send(self, query): self.query = query
        def recv(self, limit):
            assert limit == 4097
            flags = 0x8180 | (0x200 if case == 'truncated' else 3 if case == 'nxdomain' else 0)
            answer = b'\xc0\x0c' + struct.pack('!HHIH', 5 if case == 'alias' else 28 if case == 'wrong-type' else 1, 1, 60, 4) + b'\x08\x08\x08\x08'
            raw = self.query[:2] + struct.pack('!HHHHH', flags, 1, 1, 0, 0) + self.query[12:] + answer
            if case == 'mismatch': raw = b'xx' + raw[2:]
            if case == 'oversize': raw += b'x'*4097
            return raw
    monkeypatch.setattr(socket, 'socket', lambda *args: Channel())
    network = public.PublicNetwork()
    if case == 'valid':
        assert network.resolve('1.1.1.1', 1, public.time.monotonic()+2) == ('8.8.8.8',)
    else:
        with pytest.raises(DockerError): network.resolve('1.1.1.1', 1, public.time.monotonic()+2)


def test_new_runtime_modules_are_selected_by_existing_bundle_contract():
    for module in ('public_postconditions', 'public_witness', 'fixed_bindings'):
        name = f'scripts/production_integration/{module}.py'
        assert source_target(name) == name


@pytest.mark.parametrize('case', ['valid', 'peer', 'tls', 'timeout', 'oversize'])
def test_real_https_transport_pins_origin_peer_and_deadlines(setup, monkeypatch, case):
    port, _, state, calls = setup
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): assert 0 < value <= 2
        def connect(self, target): assert target == ('8.8.8.8', 443)
        def do_handshake(self):
            if case == 'tls': raise ssl.SSLCertVerificationError('bad chain')
        def getpeername(self): return ('8.8.4.4' if case == 'peer' else '8.8.8.8', 443)
        def sendall(self, request):
            assert request.startswith(b'GET /en/ HTTP/1.1\r\nHost: danil-miftakhov.dev\r\n')
            assert b'Accept-Encoding: identity' in request
        def recv(self, count):
            assert count == 8192
            if case == 'timeout': raise TimeoutError()
            if case == 'oversize': return b'x'*8192
            value, state.raw = state.raw, b''
            return value
    channel = Channel()
    class Context:
        def wrap_socket(self, raw, *, server_hostname, do_handshake_on_connect):
            assert raw is channel and server_hostname == public.HOST and not do_handshake_on_connect
            return channel
    monkeypatch.setattr(socket, 'socket', lambda *args: channel)
    monkeypatch.setattr(public, 'tls_context', lambda ca: Context())
    if case == 'valid':
        assert public.PublicNetwork().https('8.8.8.8', b'CA', public.time.monotonic()+2).startswith(b'HTTP/1.1 200')
    else:
        with pytest.raises((DockerError, ssl.SSLError, TimeoutError)):
            public.PublicNetwork().https('8.8.8.8', b'CA', public.time.monotonic()+2)


def test_tls_context_requires_certificate_chain_and_hostname(monkeypatch):
    # A real stdlib context; replace only CA parsing to avoid inventing a root.
    monkeypatch.setattr(ssl.SSLContext, 'load_verify_locations', lambda self, **kw: None)
    monkeypatch.setenv('SSL_CERT_FILE', '/untrusted/ambient')
    context = public.tls_context(b'reviewed-only')
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.get_ca_certs() == []


@pytest.mark.parametrize('case', ['valid', 'forged', 'expired', 'future', 'identity', 'policy', 'routes', 'namespace'])
def test_real_external_sshsig_receipt(tmp_path, monkeypatch, case):
    import subprocess
    from scripts.production_integration import public_witness as witness
    from scripts.reconciliation import encode
    # Disposable TEST key only, never a production principal or fixture approval.
    key = tmp_path / 'test-key'
    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    public_key = ' '.join(key.with_suffix('.pub').read_text().split()[:2])
    subject = dict(identity=['a'*32, 'daemon'], policy='reviewed', routes=['route'])
    payload = dict(subject=dict(subject), observed_at=100, expires=125)
    if case == 'expired': payload['expires'] = 105
    if case == 'future': payload['observed_at'] = 111
    if case in ('identity', 'policy', 'routes'): payload['subject'][case] = 'changed'
    result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', str(key), '-n',
                             'wrong' if case == 'namespace' else witness.NAMESPACE],
                            input=encode(payload).encode(), capture_output=True, check=True)
    if case == 'forged': payload['subject']['policy'] = 'forged'
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(encode(dict(payload=payload, signature=result.stdout.decode())))
    monkeypatch.setattr(witness, 'RECEIPT', receipt)
    monkeypatch.setattr(witness, 'protected', lambda p: None)
    installation = SimpleNamespace(verify=lambda: None, files=((witness.BINARY, 'pin'),))
    reader = witness.SignedPublicWitness(installation, public_key, clock=lambda:110)
    if case == 'valid': assert reader.verify(subject) is subject
    else:
        with pytest.raises(DockerError): reader.verify(subject)


def test_existing_registry_factory_accepts_only_trusted_code_tls_context():
    import urllib.request
    from scripts.production_integration.composition import project_router_image_registry
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    registry = project_router_image_registry(context=context)
    handler = next(h for h in registry.transport.opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
    assert handler._context is context
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_first_route_waits_for_real_signed_delivery(tmp_path, monkeypatch, setup):
    """Observer can sign only AFTER the first route exists; clocks are real."""
    import subprocess
    import threading
    import time
    from scripts.production_integration import public_witness as witness
    from scripts.reconciliation import encode
    port, routes, _, _ = setup
    key = tmp_path / 'disposable-observer'
    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    receipt = tmp_path / 'observation.json'
    monkeypatch.setattr(witness, 'RECEIPT', receipt)
    # Only production filesystem/installation ownership is simulated. Signature,
    # file publication, wall/monotonic time and subject verification are real.
    def metadata(path):
        if not path.exists():
            raise DockerError('production-installation-not-provisioned')
    monkeypatch.setattr(witness, 'protected', metadata)
    installation = SimpleNamespace(verify=lambda: None, files=((witness.BINARY, 'test-only'),))
    reader = witness.SignedPublicWitness(installation, ' '.join(key.with_suffix('.pub').read_text().split()[:2]))
    port.external, port.clock = reader, time.monotonic
    active, delivered = threading.Event(), threading.Event()
    events = {}
    network = port.network
    class FirstRoute:
        def resolve(self, *args): return network.resolve(*args)
        def https(self, *args):
            events.setdefault('activation', time.monotonic())
            active.set()
            return network.https(*args)
    port.network = FirstRoute()
    def observe():
        assert active.wait(2)
        # Separate off-host probe uses the network fixture, never a success
        # witness callback. Delay represents authenticated channel delivery.
        observer = public.PublicPostconditions(port.policy, external=None, network=network)
        subject = observer.probe(routes, port.policy.identity)
        observed = int(time.time())
        payload = dict(subject=subject, observed_at=observed, expires=observed + 30)
        signature = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'sign', '-O', 'hashalg=sha512',
            '-f', str(key), '-n', witness.NAMESPACE], input=encode(payload).encode(),
            capture_output=True, check=True).stdout.decode()
        events['signed'] = time.monotonic()
        time.sleep(0.25)
        pending = tmp_path / 'pending'
        pending.write_text(encode(dict(payload=payload, signature=signature)))
        pending.replace(receipt)
        events['delivered'] = time.monotonic()
        delivered.set()
    thread = threading.Thread(target=observe)
    thread.start()
    try:
        # Two sequential observations use the real verifier and fresh receipt.
        assert port.verify(routes, port.policy.identity) == routes
        assert port.verify(routes, port.policy.identity) == routes
        assert delivered.is_set()
        assert events['activation'] <= events['signed'] <= events['delivered'] < time.monotonic()
        assert time.monotonic() - events['activation'] < 30
    finally:
        active.set()
        thread.join(3)


def test_missing_witness_wait_is_bounded(tmp_path, monkeypatch):
    import time
    from scripts.production_integration import public_witness as witness
    import base64
    key = 'ssh-ed25519 ' + base64.b64encode(b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + bytes(32)).decode()
    monkeypatch.setattr(witness, 'RECEIPT', tmp_path / 'absent')
    def absent(path): raise DockerError('production-installation-not-provisioned')
    monkeypatch.setattr(witness, 'protected', absent)
    installation = SimpleNamespace(verify=lambda: None, files=((witness.BINARY, 'test-only'),))
    reader = witness.SignedPublicWitness(installation, key)
    start = time.monotonic()
    with pytest.raises(DockerError, match='public-witness-delivery-timeout|production-installation-not-provisioned'):
        reader.verify({}, timeout=0.15)
    assert 0.1 <= time.monotonic() - start < 1


@pytest.mark.parametrize('failure', ['public-witness-stale-or-unbound', 'public-witness-signature-invalid',
                                   'production-installation-metadata-unsafe'])
def test_delivery_wait_never_accepts_failed_evidence(monkeypatch, failure):
    import base64
    import time
    from scripts.production_integration import public_witness as witness
    key = 'ssh-ed25519 ' + base64.b64encode(b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + bytes(32)).decode()
    reader = witness.SignedPublicWitness(None, key)
    calls = []
    def failed(*args):
        calls.append(time.monotonic())
        raise DockerError(failure)
    monkeypatch.setattr(reader, '_verify', failed)
    start = time.monotonic()
    with pytest.raises(DockerError):
        reader.verify({}, timeout=0.15)
    assert time.monotonic() - start < 1
    assert len(calls) > 1 if failure == 'public-witness-stale-or-unbound' else len(calls) == 1


def test_public_probe_passes_only_remaining_delivery_budget(setup):
    port, routes, state, _ = setup
    budgets = []
    class Reject:
        def verify(self, subject, *, timeout):
            budgets.append(timeout)
            raise DockerError('public-witness-signature-invalid')
    port.external = Reject()
    original = port.network.https
    def slow(*args):
        state.now += 6
        return original(*args)
    port.network.https = slow
    with pytest.raises(DockerError, match='public-witness-signature-invalid'):
        port.verify(routes, port.policy.identity)
    assert budgets == [9]
