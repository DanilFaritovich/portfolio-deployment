"""Offline protocol/metadata/pin boundaries; real Caddy evidence is opt-in."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import socket
import stat
import struct

import pytest

from scripts.docker_adapter import DockerError
from scripts.production_integration import admin_socket as a
from scripts.production_integration.admin_contract import HOST_DIRECTORY, CONTAINER_DIRECTORY, CONTAINER_ADDRESS, HOST_ADDRESS
from scripts.production_integration.active_config import ActiveConfigTransport, active_json
from scripts.production_integration.active_runtime import PinnedCaddyRuntime
from scripts.production_integration.gateway import content
from scripts.production_integration.gateway_read import GatewaySnapshot

from test_trusted_bindings import fixed_composition
from test_production_integration import router
from test_production_contracts import registration

CID, NID = 'a'*64, 'b'*64
TOKEN = (('ancestry',), ('socket',))


def response(body=b'{}', extra=b'', status=b'200 OK'):
    return b'HTTP/1.1 ' + status + b'\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n' + extra + b'\r\n' + body


class Connection:
    def __init__(self, chunks, peer=(123, 0, 0)):
        self.chunks, self.peer, self.sent, self.timeouts = iter(chunks), peer, [], []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, value):
        assert 0 < value <= 1
        self.timeouts.append(value)
    def connect(self, address): self.address = address
    def getsockopt(self, *args): return struct.pack('3i', *self.peer)
    def sendall(self, raw): self.sent.append(raw)
    def recv(self, limit):
        item = next(self.chunks, b'')
        if isinstance(item, Exception): raise item
        assert len(item) <= limit
        return item


def install_connection(monkeypatch, chunks, peer=(123, 0, 0)):
    connection = Connection(chunks, peer)
    def connect(family, kind):
        assert (family, kind) == (socket.AF_UNIX, socket.SOCK_STREAM)
        return connection
    monkeypatch.setattr(a.socket, 'socket', connect)
    return connection


@pytest.mark.parametrize('body', [b'{}', b'{"admin":{"listen":"unix//run/portfolio-caddy-admin/admin.sock|0600"}}'])
def test_http_exact_request_and_partial_reads(monkeypatch, body):
    raw = response(body)
    c = install_connection(monkeypatch, [raw[:7], raw[7:41], raw[41:]])
    assert a._read_socket('/proc/self/fd/7/admin.sock', 123, timeout=1, limit=a.LIMIT) == body
    assert c.sent == [a.REQUEST] and c.address == '/proc/self/fd/7/admin.sock'
    assert b'GET /config/ HTTP/1.1\r\n' in c.sent[0]


def test_chunked_go_json():
    assert a.http_body(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n1\r\n{\r\n1\r\n}\r\n0\r\n\r\n', 2) == b'{}'


@pytest.mark.parametrize('raw', [b'', b'HTTP/1.0 200 OK\r\n\r\n{}', response(status=b'302 Found'),
    response(extra=b'Location: http://foreign/\r\n'), response(extra=b'Content-Length: 2\r\n'),
    response(extra=b'Transfer-Encoding: chunked\r\n'), response(extra=b'Content-Encoding: gzip\r\n'),
    response().replace(b'application/json', b'text/plain'), response()+b'foreign', response()[:-1],
    response().replace(b'Content-Length: 2', b'Content-Length: -1'),
    response(extra=b'Folded\r\n'), response(extra=b' Foo: bad\r\n'), response(extra=b'X: a\x00b\r\n'),
    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\nX: trailer\r\n\r\n',
    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\ng\r\n',
    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\nff\r\n{}\r\n0\r\n\r\n'])
def test_malformed_http_and_redirects_rejected(raw):
    with pytest.raises(DockerError): a.http_body(raw, a.LIMIT)


@pytest.mark.parametrize('peer', [(124, 0, 0), (123, 1000, 0), (123, 0, 1000)])
def test_wrong_peer_cannot_read(monkeypatch, peer):
    c = install_connection(monkeypatch, [response()], peer)
    with pytest.raises(DockerError, match='peer'): a._read_socket('/fixed', 123, timeout=1, limit=100)
    assert not c.sent


def test_response_and_header_caps(monkeypatch):
    with pytest.raises(DockerError): a.http_body(response(b'{}'), 1)
    install_connection(monkeypatch, [b'x'*16385])
    with pytest.raises(DockerError, match='header-limit'): a._read_socket('/fixed', 123, timeout=1, limit=a.LIMIT)
    install_connection(monkeypatch, [response(b'{}')[:40]] + [b'x'*65536]*17)
    with pytest.raises(DockerError): a._read_socket('/fixed', 123, timeout=1, limit=a.LIMIT)


def info(mode, uid=0, gid=0, inode=8, links=1):
    return SimpleNamespace(st_dev=1, st_ino=inode, st_uid=uid, st_gid=gid, st_mode=mode, st_nlink=links)


@pytest.fixture
def filesystem(monkeypatch):
    dirs = [info(stat.S_IFDIR | 0o755, inode=i) for i in range(4)]
    dirs[-1] = info(stat.S_IFDIR | 0o700, inode=4)
    state = SimpleNamespace(dirs=dirs, socket=info(stat.S_IFSOCK | 0o600), opened=[], closed=[])
    def open_dir(path, flags, **kwargs):
        assert flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY
        state.opened.append((path, kwargs))
        return len(state.opened)-1
    monkeypatch.setattr(a.os, 'open', open_dir)
    monkeypatch.setattr(a.os, 'fstat', lambda fd: state.dirs[fd % 4])
    monkeypatch.setattr(a.os, 'close', state.closed.append)
    monkeypatch.setattr(a.os, 'stat', lambda path, **kw: state.socket)
    return state


def test_fixed_path_descriptor_walk(filesystem):
    with a.protected_socket() as (address, token):
        assert address == '/proc/self/fd/3/admin.sock' and token[1][1] == 8
    assert [p for p, _ in filesystem.opened] == ['/', 'srv', 'portfolio', 'caddy-admin']
    assert len(filesystem.closed) == 4


@pytest.mark.parametrize('change', ['owner', 'group', 'writable', 'public', 'socket-owner', 'socket-group', 'symlink', 'regular', 'mode', 'hardlink', 'inode'])
def test_unsafe_filesystem_and_replaced_inode(filesystem, change):
    if change == 'owner': filesystem.dirs[1].st_uid = 1000
    elif change == 'group': filesystem.dirs[2].st_gid = 1000
    elif change == 'writable': filesystem.dirs[2].st_mode |= 0o020
    elif change == 'public': filesystem.dirs[-1].st_mode |= 0o005
    elif change == 'socket-owner': filesystem.socket.st_uid = 1000
    elif change == 'socket-group': filesystem.socket.st_gid = 1000
    elif change == 'symlink': filesystem.socket.st_mode = stat.S_IFLNK | 0o600
    elif change == 'regular': filesystem.socket.st_mode = stat.S_IFREG | 0o600
    elif change == 'mode': filesystem.socket.st_mode |= 0o006
    elif change == 'hardlink': filesystem.socket.st_nlink = 2
    with pytest.raises(DockerError):
        with a.protected_socket():
            if change == 'inode': filesystem.socket.st_ino += 1


def snapshot(**kw):
    return replace(GatewaySnapshot((), '', 'sha256:'+'f'*64, '2.11.7', True, True, True, CID,
                                  (TOKEN, 123, (), ('host', 'daemon'))), **kw)


@pytest.fixture
def channel(monkeypatch):
    state = SimpleNamespace(sample=snapshot(), identity=TOKEN)
    state.inspect = lambda channel: state.sample
    channel = a.UnixActiveChannel(state)
    @contextmanager
    def fixed(): yield '/fixed/admin.sock', state.identity
    monkeypatch.setattr(a, 'protected_socket', fixed)
    monkeypatch.setattr(a, 'socket_identity', lambda: state.identity)
    return channel, state


@pytest.mark.parametrize('failure', ['container', 'changed-container', 'changed-runtime', 'replaced-socket', 'unreadable', 'timeout', 'deadline', 'limit'])
def test_channel_fails_closed(channel, monkeypatch, failure):
    c, s = channel
    if failure == 'container': s.sample = snapshot(container_id='c'*64)
    if failure == 'unreadable': install_connection(monkeypatch, [PermissionError()])
    elif failure == 'timeout': install_connection(monkeypatch, [TimeoutError()])
    else: install_connection(monkeypatch, [response()])
    original = a._read_socket
    def read(*args, **kwargs):
        if failure == 'changed-container': s.sample = snapshot(container_id='c'*64)
        if failure == 'changed-runtime': s.sample = snapshot(image_config_id='sha256:'+'e'*64)
        if failure == 'replaced-socket': s.identity = ('foreign',)
        return original(*args, **kwargs)
    monkeypatch.setattr(a, '_read_socket', read)
    if failure == 'deadline':
        ticks = iter([0, 2, 3, 4])
        c.clock = lambda: next(ticks)
    with pytest.raises(DockerError): c.read_active(CID, timeout=1, limit=a.LIMIT+1 if failure == 'limit' else a.LIMIT)


def test_channel_get_only(channel, monkeypatch):
    c, _ = channel
    install_connection(monkeypatch, [response()])
    assert c.read_active(CID, timeout=1, limit=a.LIMIT) == b'{}'
    assert not hasattr(c, 'reload') and not hasattr(c, 'request')


def test_active_expired_evidence_and_changed_config():
    tick = [0]
    engine = SimpleNamespace(identity=lambda: ('host', 'daemon'))
    runtime = SimpleNamespace(inspect=lambda channel: snapshot())
    reads = [b'{"admin":{}}', b'{"admin":{}}']
    channel = SimpleNamespace(read_active=lambda *a, **kw: reads.pop(0))
    port = ActiveConfigTransport(engine, channel, runtime, SimpleNamespace(adapt=lambda r:b'{"admin":{}}'), lambda:(), clock=lambda:tick[0])
    result = port.read_active()
    image = SimpleNamespace(image_id=result.image_config_id)
    port.verify(port, image, result.version, result)
    port.verify_current(result)
    tick[0] = 6
    with pytest.raises(DockerError): port.verify(port, image, result.version, result)
    port.verify_current(result)  # Fresh runtime read compares the old baseline.
    assert port.last[2] == 0  # Revalidation never renews an expired source proof.
    with pytest.raises(DockerError): port.verify(port, image, result.version, result)
    tick[0] = 0
    reads[:] = [b'{"admin":{}}', b'{"admin":{"foreign":true}}']
    with pytest.raises(DockerError, match='changed'): port.read_active()
    assert port.last is None


@pytest.mark.parametrize('failure', [None, 'changed-runtime', 'changed-file', 'slow-socket'])
def test_slow_seal_verification_is_outside_authenticated_socket_io(monkeypatch, tmp_path, failure):
    tick, seals, reads = [0], [], []
    source = tmp_path / 'sealed.py'
    source.write_bytes(b'original')
    def verify():
        seals.append(1)
        tick[0] += 2
    installation = SimpleNamespace(files=((str(source), 'fixture'),), verify=verify)
    engine = SimpleNamespace(identity=lambda: ('host', 'daemon'))
    runtime = PinnedCaddyRuntime(installation, engine, None, None, container_id=CID, network_id=NID)
    def inspect(channel):
        if runtime._read_window is None: installation.verify()
        else: runtime._read_window.verify()
        return snapshot(image_config_id='sha256:'+'e'*64) if failure == 'changed-runtime' and len(reads) == 2 else snapshot()
    monkeypatch.setattr(runtime, '_inspect', inspect)
    channel = a.UnixActiveChannel(runtime, clock=lambda: tick[0])
    @contextmanager
    def fixed(): yield '/fixed/admin.sock', TOKEN
    monkeypatch.setattr(a, 'protected_socket', fixed)
    monkeypatch.setattr(a, 'socket_identity', lambda: TOKEN)
    def read(*args, **kwargs):
        reads.append(1)
        assert 0 < kwargs['timeout'] <= 1
        if failure == 'changed-file': source.write_bytes(b'modified')
        if failure == 'slow-socket': tick[0] += 2
        return b'{}'
    monkeypatch.setattr(a, '_read_socket', read)
    adapter = SimpleNamespace(adapt=lambda routes: b'{}')
    port = ActiveConfigTransport(engine, channel, runtime, adapter, lambda: (), clock=lambda: tick[0])
    if failure:
        with pytest.raises(DockerError): port.read_active()
        assert port.last is None
    else:
        result = port.read_active()
        assert result.container_id == CID and seals == [1, 1] and reads == [1, 1]
        assert port.last[2] == 2 and tick[0] == 4
    assert runtime._read_window is None and channel._snapshot is None


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{} {}', b'[]', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'{'*33+b'}'*33, b'x'*(a.LIMIT+1)])
def test_closed_active_json(raw):
    with pytest.raises(DockerError): active_json(raw)


def test_runtime_mount_and_generated_endpoint():
    from test_production_integration import paths
    from scripts.production_integration.runtime import CaddyRuntime
    r = CaddyRuntime('caddy@sha256:'+'f'*64, '2.11.7', paths())
    mount = r.render().model['services']['caddy']['volumes'][-1]
    assert mount == {'type':'bind', 'source':HOST_DIRECTORY, 'target':CONTAINER_DIRECTORY, 'bind':{'create_host_path':False}}
    assert content(()) == '{\n    admin '+CONTAINER_ADDRESS+'\n}\n'
    assert HOST_ADDRESS == 'unix//srv/portfolio/caddy-admin/admin.sock|0600'
    with pytest.raises(DockerError): replace(r.paths, admin_socket_directory='/srv/foreign').validate()


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    from scripts.production_integration import active_runtime as r
    from scripts.caddy_integration import image_evidence
    from scripts.production_integration.runtime import CaddyRuntime
    from test_production_integration import paths
    from test_caddy_integration import engine_document
    lock, evidence = image_evidence()
    runtime = CaddyRuntime(evidence.reference, lock['version'], paths())
    doc = engine_document(runtime.render(), evidence)
    doc.update(Id=CID, State={'Running':True, 'Pid':123})
    doc['HostConfig']['PortBindings'] = {'80/tcp':[{'HostIp':'', 'HostPort':'80'}], '443/tcp':[{'HostIp':'', 'HostPort':'443'}]}
    doc['NetworkSettings']['Networks']['portfolio-proxy']['NetworkID'] = NID
    state = SimpleNamespace(doc=doc, identity=('host','daemon'), storage=info(stat.S_IFDIR | 0o700))
    image = {'Id':evidence.image_id, 'Os':'linux', 'Architecture':'amd64',
             'RepoDigests':[evidence.reference], 'Config':__import__('json').loads(evidence.defaults_json)}
    engine = SimpleNamespace(identity=lambda:state.identity, inspect=lambda kind, value: image if kind == 'image' else state.doc)
    authority = PinnedCaddyRuntime(SimpleNamespace(verify=lambda:None), engine, runtime, evidence,
                                  container_id=CID, network_id=NID)
    monkeypatch.setattr(r, 'validate_storage_directory', lambda path, uid:None)
    monkeypatch.setattr(r, 'socket_identity', lambda:TOKEN)
    monkeypatch.setattr(Path, 'lstat', lambda path:state.storage)
    state.channel = a.UnixActiveChannel(authority)
    state.authority, state.image = authority, image
    return state


def test_pinned_runtime_binds_exact_image_engine_mounts_and_socket(pinned):
    sample = pinned.authority.inspect(pinned.channel)
    assert sample.container_id == CID and sample.channel_identity[:2] == (TOKEN, 123)
    assert len(sample.channel_identity[2]) == 4


@pytest.mark.parametrize('change', ['container', 'image', 'mount', 'runtime', 'network', 'stopped', 'pid', 'owner', 'directory-mode', 'version', 'channel'])
def test_pinned_runtime_rejects_changed_facts(pinned, change):
    s = pinned
    if change == 'container': s.doc['Id'] = 'c'*64
    elif change == 'image': s.image['Id'] = 'sha256:'+'e'*64
    elif change == 'mount': s.doc['Mounts'][-1]['Source'] = '/srv/foreign'
    elif change == 'runtime': s.doc['HostConfig']['Privileged'] = True
    elif change == 'network': s.doc['NetworkSettings']['Networks']['portfolio-proxy']['NetworkID'] = 'c'*64
    elif change == 'stopped': s.doc['State']['Running'] = False
    elif change == 'pid': s.doc['State']['Pid'] = 0
    elif change == 'owner': s.storage.st_gid = 1000
    elif change == 'directory-mode': s.storage.st_mode |= 0o005
    elif change == 'version': s.authority.runtime = replace(s.authority.runtime, version='2.11.6')
    elif change == 'channel': s.channel.runtime = None
    with pytest.raises(DockerError): s.authority.inspect(s.channel)


def test_ancestry_symlink_or_permission_error_closes_descriptor(filesystem, monkeypatch):
    original = a.os.open
    def open_dir(path, *args, **kwargs):
        if path == 'portfolio': raise OSError('symlink or permission denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(a.os, 'open', open_dir)
    with pytest.raises(DockerError, match='unavailable'): a.socket_identity()
    assert filesystem.closed == [0, 1]


def test_fixed_composition_channel_opt_in_and_engine_binding(fixed_composition, monkeypatch):
    from scripts.production_integration import composition
    installation, args = fixed_composition
    args.update(active_channel=None, gateway_runtime=None, active_pins=(CID, NID))
    executor = composition.build(installation, **args)
    transport = executor.gateway.read_port.transport
    assert type(transport.channel) is a.UnixActiveChannel
    assert type(transport.runtime) is PinnedCaddyRuntime
    assert transport.engine is transport.runtime.engine
    assert transport.channel.runtime is transport.runtime
    assert transport.runtime.container_id == CID and transport.runtime.network_id == NID


def test_host_storage_inventory_records_socket_without_opening_it(tmp_path, monkeypatch):
    from scripts.production_integration import host_policy as h
    root = tmp_path / 'caddy-admin'
    root.mkdir(mode=0o700)
    path = root / 'admin.sock'
    path.touch(mode=0o600)
    original = Path.lstat
    def lstat(p):
        return info(stat.S_IFSOCK | 0o600) if p == path else original(p)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(h, 'PATHS', (str(root),))
    monkeypatch.setattr(h, 'HOST_DIRECTORY', str(root))
    monkeypatch.setattr(h, 'HOST_SOCKET', str(path))
    monkeypatch.setattr(h, 'protected', lambda *a, **kw:None)
    monkeypatch.setattr(Path, 'open', lambda *a, **kw:pytest.fail('opened admin socket as a file'))
    records = h.storage_contents()
    record = next(r for r in records if r['path'] == str(path))
    assert record['inode'] == 8 and 'digest' not in record


def test_absolute_deadline_defeats_slow_trickle(monkeypatch):
    install_connection(monkeypatch, [b'H', b'T', b'T', b'P'])
    now = [-0.3]
    def clock():
        now[0] += 0.3
        return now[0]
    with pytest.raises(DockerError, match='timeout'):
        a._read_socket('/fixed', 123, timeout=1, limit=a.LIMIT, clock=clock)


def test_streamed_response_limit_is_enforced_before_json(monkeypatch):
    header = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 999999\r\n\r\n'
    install_connection(monkeypatch, [header, b'x'*65536])
    with pytest.raises(DockerError, match='response-limit'):
        a._read_socket('/fixed', 123, timeout=1, limit=2)
