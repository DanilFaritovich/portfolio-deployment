"""Extract an exact OCI digest from successful Docker push output, never a tag."""
import json
import os
from pathlib import Path
import re
import sys

from scripts.caddy_producer import IMAGE, REPOSITORY, WORKFLOW, BUILDER
from scripts.production_integration.release_transport import require


def receipt(raw, sha):
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha) is not None,
            'caddy-publication-sha-invalid')
    matches = re.findall(r'^sha-' + sha + r': digest: (sha256:[0-9a-f]{64}) size: [0-9]+$', raw, re.MULTILINE)
    require(len(matches) == 1, 'caddy-publication-digest-unavailable')
    return {'image': IMAGE + '@' + matches[0], 'sourceRepository': REPOSITORY, 'sourceSha': sha,
            'sourceRef': 'refs/heads/main', 'signerWorkflow': WORKFLOW, 'signerSha': sha, 'builder': BUILDER,
            'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT')}


if __name__ == '__main__':
    result = receipt(Path(sys.argv[1]).read_text(), os.environ['SOURCE_SHA'])
    Path(sys.argv[2]).write_text(json.dumps(result, sort_keys=True) + '\n')
    print('digest=' + result['image'].split('@')[1])
    print('reference=' + result['image'])
