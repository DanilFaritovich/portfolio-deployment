"""One-shot cache-only scope, real SQLite/flock, controlled Docker/GHCR sources."""
from dataclasses import asdict, replace
from types import SimpleNamespace
import json
import socket
import subprocess

import pytest

from scripts.deployment_journal import Journal
from scripts.docker_adapter import DockerError
from scripts.production_integration import image_preparation
from scripts.production_integration.artifacts import verify_oci_bytes
from scripts.production_integration.candidate import PreparedOperation, hashed
from scripts.production_integration.image_preparation import ImagePreparation, SCOPE
from scripts.production_integration.release_transport import IMAGE_REPOSITORY
from test_production_execution import setup, approve
from test_production_integration import artifact_fixture, router, registration


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*a, **k): pytest.fail('live transport attempted')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def prepared_image(setup, monkeypatch):
    s = setup
    ref, blobs, *_ = artifact_fixture()
    ref = IMAGE_REPOSITORY + '@' + ref.split('@')[1]
    evidence = verify_oci_bytes(ref, IMAGE_REPOSITORY, blobs)
    s.preflight.image = ref
    s.preflight.image_registry = lambda: SimpleNamespace(collect=lambda r: blobs)
    c = s.preflight.prepare(SCOPE).candidate
    present, calls = [False], []
    doc = {'Id': evidence.image_id, 'RepoDigests': [ref], 'Os': 'linux',
           'Architecture': 'amd64', 'Config': json.loads(evidence.defaults_json)}
    def listing(args):
        assert args == image_preparation.LIST
        calls.append('list')
        return json.dumps({'Repository': IMAGE_REPOSITORY, 'Digest': ref.split('@')[1],
                           'ID': evidence.image_id}).encode() if present[0] else b''
    def inspect(kind, reference):
        assert (kind, reference) == ('image', ref)
        assert present[0]
        calls.append('inspect')
        return doc
    engine = SimpleNamespace(identity=lambda: (c.host, c.daemon), _run=listing, inspect=inspect)
    port = ImagePreparation(installation=s.executor.installation, engine=engine)
    def pull(argv, **options):
        assert argv == ['/usr/bin/docker', '--host', 'unix:///run/docker.sock', 'image', 'pull',
                        '--platform', 'linux/amd64', ref]
        assert options['timeout'] == 120
        calls.append('pull')
        present[0] = True
    monkeypatch.setattr(image_preparation, 'run_bounded', pull)
    s.executor.image_preparation = port
    return SimpleNamespace(s=s, c=c, port=port, evidence=evidence, present=present,
                           calls=calls, doc=doc, engine=engine)


@pytest.mark.parametrize('already_present', [False, True])
def test_cache_scope_uses_same_authorization_and_preserves_application(prepared_image, already_present):
    f = prepared_image
    f.present[0] = already_present
    c, envelope = approve(f.s, f.c)
    result = f.s.executor.run(c, envelope)
    assert result.status == 'succeeded'
    assert f.calls.count('pull') == (0 if already_present else 1)
    assert f.calls.count('inspect') >= 2
    assert not f.s.world.calls and not f.s.world.present
    assert f.s.executor.run(c, envelope).status == 'blocked'
    assert f.calls.count('pull') == (0 if already_present else 1)
    with f.s.lock() as lease, Journal(f.s.directory, lease) as journal:
        assert journal.authorization_recovery() == ()
        assert journal.last_good('project-router', 'web') is None
        assert journal.db.execute('SELECT state FROM authorizations WHERE nonce=?', ('1'*32,)).fetchone()[0] == 'succeeded'


@pytest.mark.parametrize('failure', ['pull', 'unknown-cache', 'wrong-platform', 'wrong-digest', 'death'])
def test_cache_failure_fences_every_scope_and_never_replays(prepared_image, monkeypatch, failure):
    f = prepared_image
    if failure == 'pull':
        def broken(*a, **k): raise DockerError('docker-timeout', True)
        monkeypatch.setattr(image_preparation, 'run_bounded', broken)
    elif failure == 'unknown-cache': f.engine._run = lambda args: b'incomplete'
    elif failure == 'wrong-platform': f.doc['Architecture'] = 'arm64'
    elif failure == 'wrong-digest': f.doc['RepoDigests'] = [IMAGE_REPOSITORY + '@sha256:' + 'f'*64]
    elif failure == 'death':
        def lost(*a, **k): raise SystemExit('terminal lost')
        monkeypatch.setattr(image_preparation, 'run_bounded', lost)
    c, envelope = approve(f.s, f.c)
    if failure == 'death':
        with pytest.raises(SystemExit): f.s.executor.run(c, envelope)
    else:
        assert f.s.executor.run(c, envelope).status == 'recovery-required'
    before = list(f.calls)
    assert f.s.executor.run(c, envelope).status == 'blocked'
    other, signed = approve(f.s, c, nonce='2'*32)
    assert f.s.executor.run(other, signed).status == 'blocked'
    assert f.calls == before
    with f.s.lock() as lease, Journal(f.s.directory, lease) as journal:
        assert journal.authorization_recovery()


@pytest.mark.parametrize('case', ['caddy', 'tag', 'digest', 'arm', 'scope', 'grant', 'verifier'])
def test_unadmitted_images_and_scope_substitution_never_pull(prepared_image, case):
    f = prepared_image
    p = f.s.preflight.prepare(SCOPE)
    if case == 'caddy': p = replace(p, candidate=replace(p.candidate, image='docker.io/library/caddy@sha256:'+'e'*64))
    elif case == 'tag': p = replace(p, candidate=replace(p.candidate, image=IMAGE_REPOSITORY+':latest'))
    elif case == 'digest': p = replace(p, image_evidence=replace(p.image_evidence, image_id='sha256:'+'e'*64))
    elif case == 'arm': p = replace(p, image_evidence=replace(p.image_evidence, platform='linux/arm64'))
    elif case == 'scope': p = replace(p, candidate=replace(p.candidate, scope='first-bootstrap'))
    if case in ('grant', 'verifier'):
        c, envelope = approve(f.s, f.c)
        if case == 'grant':
            # An approval for another scope cannot authorize cache mutation.
            f.s.issuer.envelopes[envelope] = replace(f.s.issuer.envelopes[envelope], candidate=hashed({'other': 'scope'}))
        else: f.s.executor.release_factory = None
        assert f.s.executor.run(c, envelope).status == 'blocked'
    else:
        with pytest.raises(DockerError): f.port.preflight(p)
    assert not f.calls


def test_image_registry_and_provenance_are_mandatory(prepared_image):
    f = prepared_image
    f.s.preflight.image_registry = None
    with pytest.raises(DockerError, match='registry-unavailable'):
        f.s.preflight.prepare(SCOPE)
    assert not f.calls
