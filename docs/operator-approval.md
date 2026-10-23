# Production operator approval verification

Status: offline implementation and temporary-key verification only. No operator
key is selected or trusted, no installation or deployment occurred, and both
shipped administrative/production bindings still block. Cryptographic verification
supplies claims to `review_approval`; it supplies no installation or execution grant.

## Signed envelope v1

The envelope is ASCII canonical JSON with exactly `payload` and `signature`.
`signature` is a JSON string containing OpenSSH SSHSIG ASCII armor, including its
final newline. Payload has exactly these fields:

```json
{"candidate":"sha256:<64 lowercase hex>","expires":<integer Unix seconds>,"nonce":"<32 lowercase hex>","not_before":<integer Unix seconds>,"operator":"<approved exact principal>","version":1}
```

The bytes signed are **only the canonical payload object**, including `version`:
keys sorted lexicographically, compact `,`/`:` separators, ASCII JSON escaping,
no BOM or trailing newline. `canonical_payload()` produces these exact bytes.
The outer envelope uses the same canonical encoding. There are no unsigned
claim/algorithm/namespace/key/path fields. Unknown fields/versions, duplicate keys,
alternate whitespace/escapes, floats, booleans as numbers, exponential dates and
non-ASCII input are rejected. Times are integer POSIX UTC seconds between 0 and
253402300799; validity is `not_before <= now < expires`, at most 3600 seconds.
The nonce is 128 random bits, encoded as 32 lowercase hexadecimal characters.

Policy fixes namespace **`portfolio-deployment-operation-v1`**, raw Ed25519 keys,
SSHSIG version 1, empty reserved field and SHA-512 message hash. Certificates,
other key/hash algorithms and namespaces are rejected before cryptographic
verification. OpenSSH performs the signature verification using
[`ssh-keygen -Y verify`](https://man.openbsd.org/ssh-keygen#Y).
Envelope/payload/armor limits are 8192/1024/2048 bytes. Errors never expose verifier
output or request contents.

## Independently approved trust

An operator must generate a **separate signing key outside this repository**, on
an approved workstation or offline signing host. For example (not run by this task):

```sh
ssh-keygen -t ed25519 -a 100 -f /secure/operator-signing-key
ssh-keygen -lf /secure/operator-signing-key.pub -E sha256
```

Choose a passphrase interactively. Do not reuse the VDS login/deploy key. Decide
key custody, encrypted/offline backup, rotation and revocation separately. Never
commit private keys, real envelopes or production trust configuration.

Verify the public-key fingerprint and exact principal through an independently
authenticated channel. A key received with a candidate/envelope cannot establish
trust. Review the full public key, not merely a display name/comment. Prepare a
closed canonical ASCII trust document with exactly this shape (placeholder key):

```json
{"operators":{"approved-principal":"ssh-ed25519 <base64 raw public key>"},"version":1}
```

Only exact principals matching `[A-Za-z0-9@._-]{1,128}` and bare `ssh-ed25519`
public keys without comments/options are accepted. No patterns, CAs, certificates
or automatic principal discovery; one key per principal, maximum 16 principals,
8192 bytes total. An empty allowlist blocks.

Infrastructure must independently approve/provision the fixed root-owned,
non-writable-by-others `/etc/portfolio-deployment/operator-keys.json` and
`/usr/bin/ssh-keygen`, including their SHA-256 pins in the existing initial-install
`external_files` inventory. These become `SealedInstallation.files`; the verifier
requires **both** pins, verifies the sealed installation on every authentication,
and rereads/binds trust bytes to the pin. Existing protected-ancestor, inode,
interpreter, source/dependency and tool checks remain required. Tool provenance
and runtime-library closure require the existing independent infrastructure review.
No new tool download or installer exception exists.

A future independently reviewed fixed `bindings.build(installation)` can pass
`SSHSIGVerifier(installation)` as `ProductionExecutor.approval_verifier`. Neither
shipped binding does so. No request/env/CLI selects production trust or wiring.
Revocation requires a separately approved trust/inventory update through the
protected installation process; never edit a sealed file to bypass its pin.
Re-review already queued approvals after rotation. A newly trusted key cannot
reopen consumed ledger entries.

## Offline signing and inspection

First independently inspect the full `OperationCandidate`, its exact identity,
operation scope, host/daemon, source/artifact and state hashes. A signature over a
hash alone does not establish that the reviewed candidate is correct or fresh.
Prepare a payload on the workstation, outside the checkout. For example:

```python
from pathlib import Path
import secrets
import time
from scripts.planning_inputs import parse_snapshot
from scripts.production_integration.authorization import OperationCandidate
from scripts.production_integration.operator_approval import canonical_payload

candidate = OperationCandidate(**parse_snapshot(Path('/secure/candidate.json').read_bytes()))
now = int(time.time())
payload = dict(version=1, candidate=candidate.identity, operator='approved-principal',
               nonce=secrets.token_hex(16), not_before=now, expires=now + 900)
Path('/secure/payload.json').write_bytes(canonical_payload(payload))
```

Sign those exact bytes with the separate key:

```sh
ssh-keygen -Y sign -O hashalg=sha512 -f /secure/operator-signing-key -n portfolio-deployment-operation-v1 /secure/payload.json
```

Assemble the envelope without altering signed bytes:

```python
from pathlib import Path
from scripts.production_integration.operator_approval import canonical, closed_json, canonical_payload

raw = Path('/secure/payload.json').read_bytes()
payload = closed_json(raw, 1024)
assert canonical_payload(payload) == raw
signature = Path('/secure/payload.json.sig').read_text(encoding='ascii')
Path('/secure/envelope.json').write_bytes(canonical(dict(payload=payload, signature=signature)))
```

The separate workstation-only inspection command requires independently obtained
trust-document and OpenSSH executable pins; hashing files supplied by the requester
is insufficient. Run from the reviewed checkout with development dependencies:

```sh
python3 -m scripts.verify_operator_approval --envelope /secure/envelope.json --trust /secure/operator-keys.json --trust-sha256 "$APPROVED_TRUST_SHA256" --ssh-keygen-sha256 "$APPROVED_SSH_KEYGEN_SHA256"
```

This verifies structure, local signature, pinned local trust/tool and current time;
it reports the authenticated candidate identity with `productionAuthorized=false`.
Compare that identity to the independently reviewed candidate. It does not import
or run `ProductionExecutor`, inspect VDS/Docker, establish readiness, consume a
nonce, or install trust. It does not sign. Successful inspection remains replayable
as an inspection and is not a production authorization receipt.

Verification invokes only fixed `/usr/bin/ssh-keygen` argv with a private temporary
message/signature/allowlist, no shell, sanitized environment, five-second timeout,
and stdout/stderr discarded (zero captured output). Missing/broken tools, unknown
principals, bad pins/keys/signatures and any nonzero exit fail closed.

## Executor and remaining blockers

`SSHSIGVerifier.authenticate(envelope)` returns existing `ApprovalClaims` only
after cryptographic success. Existing `review_approval` still checks exact
`OperationCandidate.identity`, operator/nonce format, current time and the one-hour
limit. The executor repeats authentication after release verification and fresh
candidate comparison under the canonical host lock. It durably consumes nonce and
candidate in the same SQLite ledger before mutation; both identities remain spent
regardless of expiry or result. Ambiguous outcomes after consumption remain
`recovery-required`. There is no reset, replay, sign-and-deploy or bypass flag.

Before real installation: independently select and pin the operator key/principal,
trust configuration and tool; approve custody/revocation; review fixed production
and administrative bindings, protected-main release, full bundle/dependency pins,
administrative channel and host identities. Obtain separate permission for VDS
contact and installation. Before mutation, fresh source/image/Caddy provenance,
host/runtime/network/storage/recovery and readiness evidence, real registry/admission
review, backups and an exact operation approval remain mandatory. Follow the
[installation prerequisites](production-installation.md#remaining-operator-prerequisites-and-acceptance)
and [trust handoff](production-trust-readiness.md). This implementation resolves
the signature mechanism, not those trust decisions or real-host facts.
