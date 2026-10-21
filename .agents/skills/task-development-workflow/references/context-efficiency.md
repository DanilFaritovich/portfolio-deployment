# Context Efficiency Reference

Read this reference when a task involves a large repository, a continuation of existing work, large diffs, noisy CLI output, or noticeable context/token pressure.

## Repository inspection

Read the applicable `AGENTS.md` first and use it as the repository map.

Read `ARCHITECTURE.md` only when the task affects architecture, persistence, service communication, infrastructure, deployment, or external integrations.

Prefer:

```text
AGENTS.md
-> determine scope
-> search relevant symbols/paths
-> read only useful ranges
```

Search before reading large files. Prefer `rg --files -g '<glob>'` to locate files,
`rg -n 'RelevantClass|relevant_setting' affected/path` to locate relevant lines,
then `sed -n 'start,endp' file` for bounded context. Prefer `tail -n` and targeted
filters for errors over full logs. Choose the smallest useful output.

Do not use `rg -n "^"`, mass `cat`, broad `sed -n '1,$p'`, or equivalents to dump
complete large files into primary-model context. `cat` is not banned: reading
small, entirely relevant files (including `AGENTS.md`) is fine.

Before opening multiple files or one large file, search symbols/paths and inspect only
relevant ranges. If broad/noisy inspection is necessary and `inspect_task` is
available, delegate it instead of loading raw output into primary-model context.

For large files, start with the relevant range and expand only when dependencies require it.

Use one file-listing/search pass where practical instead of repeatedly enumerating the repository.

## Do not reread successful edits

After a successful edit, assume the write was applied.

Do not automatically:

- reread the complete edited file;
- run repeated `cat`, `sed`, or searches only to confirm a write;
- inspect `git diff` after every small edit.

Preferred flow:

```text
read -> understand -> edit -> targeted validation
```

Reread only when:

- an edit failed or conflicted;
- validation requires inspection;
- the next edit depends on exact current contents;
- several edits interact;
- final review reveals something unexpected.

## Continuation baseline

When continuing an already-started task branch, treat the existing reviewed task state as the continuation baseline.

Focus inspection and final review on the delta introduced by the current continuation.

Revisit earlier task changes only when:

- they were not previously reviewed;
- new work interacts with them;
- validation indicates a problem there;
- the current delta cannot be understood safely without them.

Do not treat every continuation as a fresh full-repository audit.

## Command output discipline

Use the most compact tool-native output that preserves actionable failures.

Prefer:

- quiet/summary flags;
- short tracebacks;
- filtered or machine-readable output when smaller and still useful;
- scoped commands;
- concise success summaries.

Avoid by default:

- verbose/debug modes;
- full successful test output;
- full Docker/container logs;
- dependency-install progress;
- repeated command banners;
- large successful CI logs.

Examples:

```text
pytest -q --tb=short
git status --short
docker compose config -q
```

If compact output is insufficient after a failure, rerun only the failing command/scope with the minimum extra detail needed.

Do not request verbose output proactively.

## MCP execution-worker routing

The worker is an optional *runtime capability*, not a required project dependency.
At the beginning of a nontrivial task check the tools already advertised to the
session for `free_worker.inspect_task` and `free_worker.fix_task` (possibly
namespaced). Do not repeatedly probe MCP availability or start a worker only
to verify its presence. When the tools are available, **routing the following
eligible bounded work to the worker is required**, not merely a suggestion:

- `inspect_task`: noisy tests/build/CI/Docker logs, repeated failure inspection,
  broad multi-file searches, large diffs, and other read-only exploration where
  a concise diagnosis replaces significant raw primary-model output.
- `fix_task`: deterministic formatting/lint fixes, mechanical type/test/build
  failures, and repeated `run -> diagnose -> fix -> rerun` cycles with an
  unambiguous expected behavior and a bounded scope.

Delegate a *complete bounded diagnostic or mechanical loop*, including appropriate
scoped checks, rather than one trivial command at a time. Ask the worker to return
a compact actionable summary (status, checks, changed files, path:line, blockers,
whether a primary-model decision is needed), not raw logs. Supply only the
relevant repository path and task; never forward unrelated materials or secrets.
Review compact status/diff after any `fix_task`, and independently accept its
behavior and final validation where needed. Do not rerun already-passing worker
checks when nothing relevant changed.

**Direct execution is correct** for tiny deterministic low-output commands
(e.g. `git status --short`, a single `rg -n`, small `sed` range), when worker
startup/context costs exceed the task, or when the task requires primary-model
judgment. Do not call the worker just to satisfy a quota. When a task qualifies
for delegation but you execute it directly, give a concrete short reason.

**Do not delegate** architecture, security/access/secret decisions, persistence or
migration and unknown-state recovery, concurrency and transaction design, public
contracts, production/live VM/Docker/deployment mutations, credential handling,
final acceptance, or Git staging/commit/push/PR/merge. These are owned by the
primary agent. A worker may inspect *sanitized* logs from these areas when
read-only and bounded; the primary agent decides what to do. Never send tokens,
private keys, authentication files, or sensitive host journals to the worker.

If the MCP is missing, times out, rejects the task, or has unsafe permissions,
record a brief reason and continue directly with scoped commands. Do not
automatically install, configure, restart, or retry the MCP as a side effect;
request separate approval for troubleshooting it. Respect the worker's own tool
and permission contract. Its instruction-only read-only mode is not an OS sandbox.

## Delegated validation budget

Run safe autofixes before read-only validation. During iteration run only affected
tests/checks; let `fix_task` handle mechanical correction loops when eligible.
Run the project's aggregate `make check` once after stabilization, unless a
subsequent relevant change invalidates its result. If the worker already ran an
identical successful validation on unchanged sources, do not duplicate it.
Keep genuine live acceptance separate from offline validation.

## Agent narration

Keep intermediate narration short and decision-oriented.

Report only:

- meaningful findings;
- plan/scope changes;
- failures and their causes;
- important validation results.

Do not restate every successful tool call or routine read/fetch/status result.

## Final diff budget

Do not inspect a full diff after every edit.

Start final review with compact metadata:

```text
git status --short
git diff --cached --check
git diff --cached --stat
git diff --cached --name-status
```

If the patch is modest, review it once in full.

If the patch is large:

- prioritize the current continuation delta when applicable;
- review targeted diffs by risk area;
- do not dump the whole patch into context;
- if output is truncated, inspect only missing/high-risk paths rather than repeating the full diff.

Typical risk areas:

- application/architecture;
- infrastructure/configuration;
- tests;
- security-sensitive code.

Documentation-only changes usually need only targeted review when relevant.
