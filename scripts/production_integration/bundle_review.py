"""Independent workstation review using existing protected-main/App-bound checks.

Install/review this verifier independently of the candidate bundle. No CLI policy,
approval issuer, VDS transport or new signature scheme. GitHub policy A/B remains
unapproved; defaults cannot verify a release or authorize initial installation.
"""
from dataclasses import dataclass
import hashlib
import re

from installation.operator_bootstrap import read_bundle, digest
from scripts.docker_adapter import DockerError
from scripts.production_integration.candidate import hashed
from scripts.production_integration.install_bundle import source_target
from scripts.production_integration.release import MANAGER
from scripts.production_integration.release_source import ProtectedSource


@dataclass(frozen=True)
class ManagerBundleVerification:
    manager_sha: str
    bundle_sha256: str
    evidence_identity: str


class ManagerBundleVerifier:
    """github is authenticated GitHubRead; pins are independent installed policy.

    asset_pins require independently verified origin for ALL runtime/vendor/tool
    bytes, including the official validation Caddy binary. Hashes supplied by the
    bundle or by its builder are not accepted as that independent policy.
    """
    def __init__(self, *, github=None, checks=(), asset_pins=(), bindings_pin=None):
        self.github, self.checks = github, tuple(checks)
        self.assets, self.bindings_pin = tuple(asset_pins), bindings_pin

    def verify(self, raw):
        try:
            return self._verify(raw)
        except DockerError:
            raise
        except Exception:
            raise DockerError('production-manager-bundle-unverified') from None

    def _verify(self, raw):
        if self.github is None or not self.checks or not self.assets:
            raise DockerError('production-manager-independent-review-unconfigured')
        for check in self.checks:
            check.validate()
            if check.repository != MANAGER:
                raise DockerError('production-manager-review-policy-invalid')
        if len({(c.name, c.app_id) for c in self.checks}) != len(self.checks):
            raise DockerError('production-manager-review-policy-invalid')
        manifest, files = read_bundle(raw)
        sha = manifest['manager_sha']
        source = ProtectedSource(self.github)
        source.collect(MANAGER, sha, self.checks)
        commit = self.github.get(MANAGER, '/git/commits/' + sha)
        tree_sha = commit['tree']['sha']
        if commit['sha'] != sha or not re.fullmatch(r'[0-9a-f]{40}', tree_sha):
            raise DockerError('production-manager-source-tree-unverified')
        tree = self.github.get(MANAGER, '/git/trees/' + tree_sha + '?recursive=1')
        if (tree['sha'] != tree_sha or tree['truncated'] is not False or
            not isinstance(tree['tree'], list) or len(tree['tree']) > 4096):
            raise DockerError('production-manager-source-tree-incomplete')
        expected, paths = {}, set()
        for item in tree['tree']:
            path = item['path']
            if path in paths:
                raise DockerError('production-manager-source-tree-incomplete')
            paths.add(path)
            if item['type'] == 'tree':
                continue
            target = source_target(path)
            if target is None:
                continue
            if (item['type'] != 'blob' or item['mode'] not in ('100644', '100755') or
                not re.fullmatch(r'[0-9a-f]{40}', item['sha']) or target in expected):
                raise DockerError('production-manager-source-tree-unverified')
            expected[target] = item['sha']
        assets = dict(self.assets)
        if (len(assets) != len(self.assets) or set(files) != set(expected) | set(assets) or
            set(expected) & set(assets) or 'tools/caddy' not in assets or
            any(not (n.startswith('vendor/') or n == 'tools/caddy') for n in assets)):
            raise DockerError('production-manager-dependencies-unverified')
        for name, (data, _) in files.items():
            if name in assets:
                if digest(data) != assets[name]:
                    raise DockerError('production-manager-dependencies-unverified')
            elif name == 'bindings.py' and self.bindings_pin is not None:
                if digest(data) != self.bindings_pin:
                    raise DockerError('production-manager-bindings-unverified')
            elif hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != expected[name]:
                raise DockerError('production-manager-source-content-mismatch')
        # Repeat source checks after reading tree/dependencies; no saved proof.
        source.collect(MANAGER, sha, self.checks)
        return ManagerBundleVerification(sha, digest(raw), hashed({'manager': sha, 'tree': tree_sha,
                                                                 'bundle': digest(raw), 'assets': sorted(self.assets)}))
