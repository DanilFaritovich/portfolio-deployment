# Trust, Credentials and Automatic Deployment Triggers

Read for GitHub Actions, GHCR, artifact notifications, approvals, secret distribution or host access.

## Trust model

- GitHub application repositories publish images after validated trusted commits. Publication is not production activation.
- The deployment repository holds desired revisions and the policy for accepting image updates. The controller must authenticate the image source and pin the selected digest.
- Keep registry-read identity, deployment/write identity and gateway reload identity separate where feasible. Never expose deploy credentials to untrusted PR execution.
- Scoped GitHub Environments and restricted branches/tags help guard production workflows. Use least-privilege `GITHUB_TOKEN` and explicit `permissions` at job level.
- Protect GitHub Actions from untrusted manifest-influenced shell interpolation; pass parsed fields as data through validated arguments, not command strings.

## Trigger options

1. **Manual foundation:** change reviewed manifest -> CI plan -> approved deploy.
2. **Protected automatic update:** trusted image publish -> authenticated digest proposal -> policy validation/reviewed desired revision -> gated reconcile.
3. **Polling:** controller periodically checks eligible published digests and produces proposals or applies only under an explicitly authorized policy.

Select one initial path with a documented trust chain. Do not implement both webhooks and polling unless needed. A GitHub Actions job cannot assume untrusted repository dispatch input is an authorized command to update an arbitrary app.

## GHCR

- Verify the image name, full digest, architecture and pull permissions.
- Private GHCR artifacts require a suitable read credential; do not rely on a workflow's transient token being valid for a remote host indefinitely.
- Retain the old known-good image digest long enough for a bounded rollback; document registry retention/pruning policy.
- Respect producer's build-time public origin versus runtime settings. A domain mismatch may require a new build, not an env-only update.
- Capture exact digest/revision in deploy record and logs, without printing tokens.

## Host access

- Reuse existing server provisioning boundaries. A deploy account with unrestricted Docker socket access is effectively host-privileged even if SSH login is non-root.
- Prefer narrowly authorized remote commands or constrained deployment services; do not mount `/var/run/docker.sock` into an internet-facing service or a gateway.
- Treat SSH host identity/known_hosts as pinned trust, not dynamically accepting whichever host answers.
- Serialize mutating deploys and require explicit approval before initial production connection. Do not automatically retry after unknown-state SSH/network failures.
- Make recoverable state and operator runbook available independent of the running application or gateway.
