"""Recovery reuses the original authenticated two-attestation release contract."""
from types import SimpleNamespace

from scripts.cd.rollout_recovery import verify_original_release
from scripts.production_integration.release import MANAGER, PRODUCER
from test_release_collector import fixture, offline


def test_original_rollout_release_is_verified_without_future_cd_attestation():
    candidate, docs, transport, crypto, verifier, request = fixture()
    docs[MANAGER, '/branches/main/protection']['required_status_checks']['checks'][0]['context'] = 'Offline checks'
    docs[MANAGER, '/commits/'+candidate.manager_sha+'/check-runs?filter=latest&per_page=100']['check_runs'][0]['name'] = 'Offline checks'
    docs[MANAGER, '/actions/runs?head_sha='+candidate.manager_sha+'&per_page=100']['workflow_runs'][0]['path'] = '.github/workflows/offline.yml'
    original = verifier.collector
    fixed = SimpleNamespace(producer_sha=candidate.producer_sha, signer_sha=candidate.producer_sha,
        image=candidate.image, producer_checks=tuple(c for c in original.checks if c.repository == PRODUCER),
        slsa_builder=original.slsa_builder, publication_check=original.publication_check)
    proof = verify_original_release(candidate, fixed,
        github=original.github, registry=original.registry, crypto=crypto)
    assert proof.request == request and not proof.blockers
    assert crypto.called and len(crypto.expectations) == 2
    assert crypto.expectations[1]['expectedPredicate']['runId'] == '100'


def test_public_rendezvous_renews_challenge_until_matching_fresh_receipt(tmp_path, monkeypatch):
    from scripts.cd.rollout_recovery import await_public_witness
    from scripts.production_integration.operator_approval import canonical
    monkeypatch.setattr('scripts.production_integration.installation.protected', lambda path: None)
    receipt = tmp_path / 'receipt.json'
    elapsed, publications = [0.0], []
    subject = {'policy': 'exact-reviewed-subject'}
    def publish():
        publications.append(elapsed[0])
        payload = {'subject': subject, 'observed_at': 99 if len(publications) < 3 else 101, 'expires': 130}
        receipt.write_bytes(canonical({'payload': payload, 'signature': 'not-authority-for-this-wait'}))
    def sleep(seconds):
        elapsed[0] += seconds
    assert await_public_witness(publish, subject, receipt=receipt,
        clock=lambda: elapsed[0], wall=lambda: 100 + elapsed[0], sleep=sleep) is None
    assert publications == [0.0, 0.5, 1.0]
