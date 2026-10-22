"""Production adapter for the existing pinned gh/Sigstore verifier.

Bundles come from the authenticated fixed-repository API. Verification runs on
local subject bytes with a separately provisioned, digest-pinned trust root and
binary. No token, Docker configuration, proxy or user gh configuration is passed.
No network download/provisioning occurs here. Raw verifier output stays private.
"""
import hashlib
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

from scripts.application_acceptance import attestation_command, verified_statement, verify_gh_version
from scripts.docker_adapter import DockerError
from scripts.production_integration.release import PRODUCER
from scripts.production_integration.release_transport import LIMIT, require


def bounded_process(args, directory):
    env = {'HOME': str(directory), 'GH_CONFIG_DIR': str(directory), 'GH_HOST': 'github.com',
           'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'GH_NO_EXTENSION_UPDATE_NOTIFIER': '1'}
    try:
        with subprocess.Popen(args, env=env, cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            try:
                output = bytearray()
                deadline = time.monotonic() + 30
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        require(remaining > 0, 'production-release-crypto-timeout')
                        ready = selector.select(remaining)
                        require(ready, 'production-release-crypto-timeout')
                        chunk = process.stdout.read1(65536)
                        if not chunk:
                            break
                        output.extend(chunk)
                        require(len(output) <= 4 * LIMIT, 'production-release-crypto-output-limit')
                require(process.wait(timeout=max(.01, deadline - time.monotonic())) == 0,
                        'production-release-signature-unverified')
                return bytes(output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    except (OSError, subprocess.TimeoutExpired):
        raise DockerError('production-release-crypto-unavailable') from None


class AttestationVerifier:
    """Installed code dependency; pins are reviewed installation data, never CLI flags.

    The trusted root must include the appropriate Fulcio/Rekor/TSA trust material.
    gh verifies the DSSE signature, certificate identity and witnessed timestamps;
    policy constraints reuse acceptance's exact signer/source flags and selector.
    """
    def __init__(self, binary, binary_sha256, trusted_root, root_sha256):
        self.binary, self.binary_hash = Path(binary), binary_sha256
        self.root, self.root_hash = Path(trusted_root), root_sha256

    def verify(self, subject, image, identity, expectations, bundles):
        require(type(subject) is bytes and len(subject) <= LIMIT)
        with tempfile.TemporaryDirectory(prefix='pd-release-crypto-') as temporary:
            directory = Path(temporary)
            for source, digest, name, bound in (
                (self.binary, self.binary_hash, 'gh', 128 * LIMIT),
                (self.root, self.root_hash, 'root.jsonl', LIMIT),
            ):
                require(source.is_absolute() and not source.is_symlink(), 'production-release-crypto-installation-invalid')
                with source.open('rb') as stream:
                    raw = stream.read(bound + 1)
                require(0 < len(raw) <= bound and hashlib.sha256(raw).hexdigest() == digest,
                        'production-release-crypto-installation-invalid')
                (directory / name).write_bytes(raw)
            binary = directory / 'gh'
            binary.chmod(0o500)
            try:
                verify_gh_version(bounded_process([str(binary), '--version'], directory))
            except Exception:
                raise DockerError('production-release-crypto-version-invalid') from None
            artifact = directory / 'subject'
            artifact.write_bytes(subject)
            require(isinstance(bundles, list) and 0 < len(bundles) <= 10,
                    'production-release-attestation-unavailable')
            bundle_file = directory / 'bundles.jsonl'
            from scripts.reconciliation import encode
            raw = ('\n'.join(encode(bundle) for bundle in bundles) + '\n').encode()
            require(len(raw) <= LIMIT, 'production-release-response-too-large')
            bundle_file.write_bytes(raw)
            statements = []
            for expectation in expectations:
                args = attestation_command(image, PRODUCER, identity, expectation['predicateType'], str(binary))
                # Local bytes have the exact selected digest; no registry resolver,
                # tag lookup, authentication or outbound request is needed by gh.
                args[3] = str(artifact)
                args.extend(['--bundle', str(bundle_file), '--custom-trusted-root', str(directory / 'root.jsonl'),
                             '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com'])
                raw = bounded_process(args, directory)
                try:
                    statements.append(verified_statement(raw, image, expectation))
                except Exception:
                    raise DockerError('production-release-signed-claims-mismatch') from None
            return tuple(statements)
