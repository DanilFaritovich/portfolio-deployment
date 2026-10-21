# Stage 08 — offline CI and successful VM completion

## Pull Request checks

`.github/workflows/offline.yml` runs on `pull_request` into `develop` and `main`,
including fork PRs, on Ubuntu 24.04 with Python 3.12.12. Checkout/setup-python are
pinned by commit; checkout does not persist credentials. The token has only
`contents: read`. No repository/environment secrets, privileged jobs, publishing,
deployment, Docker or Multipass commands are used. Superseded runs are cancelled.

Provisioning downloads pinned Python dependencies (including their transitive
dependencies) and actionlint 1.7.7 with a checked archive SHA-256. Pip's download
cache is keyed by `requirements-dev.txt`. Provisioning needs network access;
`make workflow-check` and `make check` then run offline and do not repair source.
The final Git diff check detects tracked-file mutations. Tests use temporary
fixtures, mocks and SQLite files; they do not run real VM scenarios.

`make check` already covers the full offline suite, so no `make ci` alias is
introduced. `make workflow-check` requires an installed actionlint; it fails
clearly when missing. Optional shellcheck/pyflakes integrations are disabled for
deterministic results across local and hosted environments.

For Linux amd64, provision the same workflow validator separately:

```bash
mkdir -p /tmp/pd-actionlint-1.7.7
curl --fail --silent --show-error --location \
  https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz \
  --output /tmp/pd-actionlint-1.7.7/actionlint.tar.gz
echo '023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757  /tmp/pd-actionlint-1.7.7/actionlint.tar.gz' | sha256sum --check
tar -xzf /tmp/pd-actionlint-1.7.7/actionlint.tar.gz -C /tmp/pd-actionlint-1.7.7 actionlint
make workflow-check ACTIONLINT=/tmp/pd-actionlint-1.7.7/actionlint
```

Repository administrators should require **Offline CI / Offline checks** for
`develop` and `main`, require PR review, and dismiss stale approvals after changes.
Branch protection/rulesets are a manual repository setting, not configured by this
change. CI checks GitHub's PR merge revision; each new PR revision reruns checks.
There is no deployment/source trigger or production eligibility gate yet.

## Caddy regression boundary

Before `/load`, every active route must match the closed desired catalog. Every
retained route must also have the current endpoint and pass both proxy and direct
upstream health checks. Failure blocks activation before a snapshot or HTTP load.
The target route's old endpoint may be stale: it still passes structural/ownership
validation, while the caller supplies readiness for the replacement. After load,
all routes must pass observations without that exception. Candidate validation,
snapshot preservation and the final active-config comparison remain mandatory.

Offline regressions cover retained proxy/upstream/transport failure, stale/missing
retained endpoints, successful replacement of an unavailable old target and exact
preservation of the sentinel route. These do not constitute another real VM run.

## Manual successful-session completion — review before execution

This is an operator procedure, not a new cleanup command. **It has not been run as
part of Stage 08.** Existing `e2e-clean` only handles scoped containers/networks;
it does not export evidence or delete a VM. No automatic VM deletion is added.

Use this procedure only after separate authorization to inspect/export the chosen
successful session. Stopping and deleting it each require their own explicit
confirmation after evidence review. Stop at any missing evidence, identity
mismatch, active controller, failed command or ambiguous result. Never retry a
mutation with an unknown outcome. Do not use this for failed/unknown sessions,
fixture-builder VMs, or another session's resources.

1. Select the exact 16-hex session, full state/fixture directory paths and VM name
   from its original receipt. Do not assume `current.json` still selects it.
   For the accepted Stage 07 run these are documented in
   [the acceptance record](local-e2e.md#verified-local-acceptance). Read the named
   `<session>.json`: require `phase=complete`, `status=passed`, matching `session`
   and `vm=pd-e2e-<session>`, and a nonempty pinned `identity`. Its `result` and
   the guest `report.json` must both show real execution (`mode=real`,
   `realE2E=performed`), `status=passed`, and all 15 distinct expected scenario
   names passed. Compare host/guest results, not just exit codes.

2. Arrange exclusive maintenance: stop launching E2E/fixture/recovery controllers
   on the host and in the guest; inspect process command lines and parent/child
   trees (`ps -eo pid,ppid,args`). Check there is no outstanding Multipass launch,
   exec, transfer, stop or delete operation for this VM. Do not kill an unidentified
   process or remove a lock to proceed. Hold the existing fixed host control lock
   plus the selected state and fixture `controller.lock` files for the entire
   export/review/stop/delete window, using `Store(...).locked()` in this order:
   `control.root()`, selected state directory, selected fixture directory. For
   example, in a Python terminal from this checkout:

   ```python
   from contextlib import ExitStack
   from scripts.e2e.host_state import Store
   from scripts.e2e import control
   held = ExitStack()
   control_store = held.enter_context(Store(control.root()).locked())
   state_store = held.enter_context(Store('/absolute/selected-state-directory').locked())
   fixture_store = held.enter_context(Store('/absolute/selected-fixture-directory').locked())
   # Keep this terminal alive until completion; never unlink controller.lock.
   ```

   Read the selected receipt again under these locks and compare its bytes/hash
   to step 1. Preserve all other receipts and closure records; do not invoke
   recovery/closure, clear `active.json`, change `current.json`, or bypass a fence.
   A historical failed session is not converted into a success by this procedure.
   Avoid wrappers that reacquire these locks while they are held.

3. Save `multipass version`, `multipass list --format json`, and
   `multipass info "$VM" --format json` in a new private host evidence directory.
   Require exactly the selected name, Running state and no host mounts. Verify
   `/etc/portfolio-e2e.json`, `/etc/machine-id`, `/etc/hostname` and
   `/var/lib/portfolio-e2e/<session>/identity.json`: session, machine-id, hostname
   and Docker daemon ID must match the original host receipt. Obtain the actual
   daemon ID with `docker --host unix:///run/docker.sock info --format '{{.ID}}'`
   **inside this VM only**. Use the existing `VM.guest()` identity guard for guest
   reads where practical; name equality alone is insufficient.

4. Hold the guest's existing `scripts.deployment.host_lock()` in a separate root
   Python terminal (`cd /opt/portfolio-e2e`), keeping it alive through export and
   until stop. This uses `/tmp/portfolio-deployment-locks/host.lock`; never unlink
   it. Check guest process trees again after locking. Do not run the scenario
   runner, deploy, clean, recovery or journal migration commands during export.
   Intentional crash/unknown scenario journals are expected even in a successful
   report: preserve them without completing, resetting or replaying their runs.

5. While quiescent, archive **the entire selected**
   `/var/lib/portfolio-e2e/<session>/` tree: identity, report, baseline, generated
   registry, gateway snapshots, every journal directory and all SQLite database,
   WAL/SHM/sidecar files. Also include `/opt/portfolio-e2e/` source, `source.json`,
   image lock/raw manifests and requirements (exclude only `.venv` and
   `__pycache__`), the VM marker, machine-id and hostname. Capture current Caddy
   `/config/` JSON, service status and bounded service logs as separate metadata.
   Use a new session-specific private export directory; refuse existing files,
   symlinked source roots or an unexpected file type. Archive relative paths with
   `tar -C /`; never delete, truncate or change source evidence permissions.

6. Compute archive byte size and SHA-256 **in the guest**, retain that descriptor,
   and copy the archive using `multipass transfer "$VM:<exact-export-file>"`
   to a new private host directory outside the checkout and VM. For Snap, use
   a supported ordinary home path such as
   `~/PortfolioDeploymentE2E/<session>/completion`; do not loosen HOME permissions.
   Only the export copy may be made readable to the VM's transfer user. Compare
   host byte size and SHA-256 with the guest descriptor. No match means no stop
   or deletion. Keep the descriptor separately from the archive.

7. Copy the complete selected host state and fixture directories, source archive/
   preparation evidence and the fixed control directory into that host evidence
   directory, retaining original files and lock inodes. Include closure receipts
   and any evidence they reference (including earlier failed-session evidence)
   so references remain reviewable. Create a SHA-256 inventory with relative
   filenames for all exported regular files, and verify it with `sha256sum -c`.
   Inspect archive member names/types before extracting into an empty private
   directory as an unprivileged user: reject absolute/traversal paths, links,
   devices and members outside the selected roots. Confirm required artifacts
   exist, parse reports/receipts again and compare identities/results. On a
   **disposable extracted copy** of each SQLite database, run
   `PRAGMA integrity_check` and require `ok` with its copied WAL alongside it;
   keep the original archive and raw journal bytes untouched. Hashes alone do
   not prove a complete or usable backup. Retain a second verified copy outside
   the VM's backing storage and record its location before permanent deletion.

8. Prepare a completion record outside the VM: session/name, pinned identity,
   tested source revision, receipt hashes, 15/15 result, archive hash/size,
   inventory verification and SQLite results, backup locations, controller/lock
   checks, timestamp and operator. Recheck identity, receipts and absence of
   controllers before requesting approval. Keep the host and guest locks held.
   Any change invalidates the review. Do not rename/recreate the selected VM.

9. **Separate stop approval:** display the exact VM, machine-id, session and
   archive hash to the reviewer. Only after approval, set and validate `SESSION`
   and `VM` in the operator shell and require an exact typed token:

   ```bash
   [[ "$SESSION" =~ ^[0-9a-f]{16}$ && "$VM" == "pd-e2e-$SESSION" ]] || exit 1
   read -r -p "Type stop:$VM after evidence approval: " completion_token
   [[ "$completion_token" == "stop:$VM" ]] || exit 1
   multipass stop "$VM"
   multipass info "$VM" --format json
   ```

   Require Stopped and save output. Guest lock terminal disconnects on stop;
   retain host locks. If stop fails/times out, inspect inventory and record the
   outcome; do not automatically proceed, restart or repeat.

10. **Separate permanent deletion approval:** review the completed archive and
    stopped identity/name record again. Verify no controller or intervening VM
    replacement/start exists. If this cannot be established, stop for review.
    Only after explicit approval for permanent loss of this selected VM:

    ```bash
    [[ "$SESSION" =~ ^[0-9a-f]{16}$ && "$VM" == "pd-e2e-$SESSION" ]] || exit 1
    read -r -p "Type delete:$VM after permanent deletion approval: " completion_token
    [[ "$completion_token" == "delete:$VM" ]] || exit 1
    multipass delete --purge "$VM"
    multipass list --format json
    ```

    `delete --purge <name>` permanently removes only that named instance, as
    described in the [Multipass delete reference](https://canonical.com/multipass/docs/latest/reference/command-line-interface/delete/).
    Never use `delete --all`, bare/global `purge`, name globs, or a loop over VMs.
    Compare inventory with the saved list: selected VM absent, others unchanged.
    Record command results and approvals in the external completion record,
    reverify the backup inventory, then release host locks (`held.close()`).
    Keep every original host receipt, journal copy and recovery fence in place.

This procedure is ready for operator review; live export, restore inspection,
stop and deletion remain unperformed until separately authorized. It does not
authorize production operations or claim backup/cleanup acceptance from offline CI.
