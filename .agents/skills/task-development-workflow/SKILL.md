---
name: task-development-workflow
description: Core development workflow with task branches created strictly from develop, local-only commits, opt-in documentation, and automatic ARCHITECTURE.md maintenance for material architecture changes.
---

# Task Development Workflow

Use this core workflow for normal feature, fix, refactor, chore, and documentation tasks unless project-local instructions are stricter.

## Core goals

- implement the complete requested scope;
- read only context required for the task;
- preserve unrelated user changes;
- follow existing architecture and conventions;
- normalize before read-only validation;
- use targeted checks during development;
- route eligible noisy inspections and mechanical fix/validation loops through an
  available MCP execution worker; keep design, security and live operations here;
- avoid repeated reads/checks;
- leave remote publication, Pull Requests, and merge to the developer unless explicitly requested;
- treat code completion, offline CI, real-environment acceptance, documentation, and release as distinct checkpoints with their own evidence and approval.

## Core workflow

```text
inspect branch/worktree
-> resolve develop (create from main if missing) -> task branch FROM develop, or continue current task branch
-> determine scope and read relevant context
-> implement complete change
-> make fix / component fix
-> targeted validation
-> make check once
-> optional make verify
-> requested documentation; update existing ARCHITECTURE.md if architecture materially changes
-> final review
-> local commit
-> STOP (push/PR/merge only on explicit request)
```

## Multi-stage infrastructure and release handoff

For an infrastructure task, select the existing `ansible` skill and load its staged-initialization or VM-E2E reference only when those phases are requested. A passing offline `make check` or merged PR does not authorize host contact or prove successful deployment. Capture separately what the agent implemented, what CI verified, and what the operator actually ran. When a feature is accepted and a versioned release is requested, use [references/git-delivery.md](./references/git-delivery.md) for the `develop → main → tag/release` boundary. Neither PR creation nor a successful test authorizes a merge or tag by itself.

## Explicit task stop points

The default delivery boundary is a local commit, not remote publication. Explicit user instructions can request an earlier stop or separately authorize a push/PR.

If the caller explicitly requests a staged workflow such as:

```text
implement only
-> developer reviews/runs checks
-> later request performs commit/PR
```

stop at the requested stage. In particular:

- do not run validation commands when the caller reserved validation for manual execution;
- do not contact external/live systems merely because later validation would normally do so;
- do not stage or commit if the caller reserved those steps; do not push, open a Pull Request, or wait on CI without an explicit request;
- still prepare the code/configuration/documented commands required for the developer's next manual step;
- report later workflow stages as pending rather than treating them as failed.

A later explicit request may resume from the verified working tree without restarting completed implementation work.

## Start safely

The **only base for new task branches is `develop`**. Never create a task branch directly from `main`, even when `main` is checked out. Do not treat `main` as a fallback development base.

Before editing:

1. Check the current branch, local/remote `develop` availability, and staged, unstaged, and untracked changes.
2. If already on a non-base task/development branch, **continue there**; do not automatically create another branch or rebase/switch it.
3. If on `develop`, create a descriptive task branch from the current `develop` tip before editing.
4. If on `main`: use an existing local `develop` (or materialize a local tracking `develop` from `origin/develop`, when only the remote exists); then create the task branch **from `develop`**. If no local or remote `develop` exists, create **local `develop` from `main` first**, then create the task branch from that new `develop`. Never push the newly created `develop` implicitly.
5. Preserve all unrelated changes; if switching branches would overwrite work, stop and ask rather than forcing checkout, stashing, resetting, rebasing, or discarding it. Do not silently update or rewrite existing branches.
6. On detached HEAD, ambiguous/unrelated branches, or unresolved base state, ask before modifying files.

Never perform normal task development directly on `develop` or `main`; task commits remain local unless remote delivery was explicitly requested.

## Load only relevant context

Read project-local `AGENTS.md` first, then this core workflow skill. **Before
starting nontrivial work, select and read every task-relevant installed
`SKILL.md`** using the project's skill map or available skill inventory.
For each selected skill, load the references whose documented trigger applies
to the task; do not assume reading `SKILL.md` alone satisfies its requirements.
In particular, load:

- `references/context-efficiency.md` for MCP delegation, long/noisy
  investigation, repeated errors, or context pressure;
- `references/validation.md` for code, tests, build, CI or integration changes;
- `references/git-delivery.md` before branch, commit, push, PR or release work.

If an applicable installed skill or required reference is unreadable, say so
rather than silently skipping it. Do not load irrelevant skills, repeatedly
reread unchanged instructions, or download/update upstream standards during
normal tasks; synchronize only through an explicitly requested standards-sync.

Read `ARCHITECTURE.md` only when the task affects architecture, persistence, service communication, infrastructure, deployment, or external integrations.

Inspect only relevant source, tests, interfaces, configuration, analogous implementations, and documentation.

For large repositories, continuations, noisy commands, large diffs, or when an
MCP execution worker is advertised, read
[references/context-efficiency.md](./references/context-efficiency.md).

At the start of a nontrivial task, determine whether the registered MCP tools include
`free_worker.inspect_task` and `free_worker.fix_task` (tool names may be namespaced).
When available, **delegate eligible bounded, high-output inspection to `inspect_task`
and unambiguous mechanical fix/check loops to `fix_task`**; do not substitute
repeated direct commands without a concrete reason. Delegate a whole bounded loop,
not each tiny shell command. Direct small, low-output checks remain appropriate.
Keep architecture, security, secrets, state recovery, live operations, Git delivery
and final acceptance with the primary agent. If the worker is unavailable or
unsuitable, proceed directly, mention the reason briefly and never install or
reconfigure it as a side effect. See the reference for routing and failure cases.

## Determine and implement scope

Before editing, identify:

- required behavior;
- affected components/files;
- existing tests;
- relevant project conventions;
- whether documentation was explicitly requested, or existing `ARCHITECTURE.md` must be updated because the task materially changes the architecture.

Avoid unrelated refactoring.

Implement every requirement in the requested scope and prefer existing patterns over parallel abstractions.

## Validation

When behavior, tests, build configuration, integration boundaries, or CI are affected, read [references/validation.md](./references/validation.md).

Default local sequence:

```text
implementation
-> safe deterministic fix
-> changed/new targeted checks
-> targeted corrections
-> make check once
-> make verify only when justified
```

Do not use the full CI pipeline as a normal debugging loop.

Do not repeat a successful check unless later changes could affect it.

## Documentation

Do **not** create, update, or refresh routine documentation without an explicit request (including a request to synchronize agent standards/instructions). **Exception:** if implementation materially changes architecture—module/component responsibilities, boundaries/dependencies, major data or control flows, persistence/integration contracts, or deployment topology—make a minimal accurate update to an **existing** `ARCHITECTURE.md` after implementation stabilizes, even without a separate documentation request. Small refactors, file moves without changed responsibilities, and implementation details alone do not trigger this exception. Do not auto-create `ARCHITECTURE.md`, rewrite unrelated sections, or use the exception to change README/`docs/`/`AGENTS.md`.

Use the documentation skill for details.

## Final review

Start with compact status/diff metadata.

Do not dump a large full patch into context. Use targeted risk-area review when the patch is large.

For detailed context/diff rules, read [references/context-efficiency.md](./references/context-efficiency.md).

## Git delivery

When starting/continuing branches, committing, pushing, preparing PRs, or handling CI, read [references/git-delivery.md](./references/git-delivery.md).

Normal local endpoint:

```text
task branch -> validated changes -> local commit -> STOP
```

Stage only task-related files. **Do not push, create/update a Pull Request, or merge without a specific explicit user request.** An explicit request to create a PR also authorizes pushing its task branch, but not a merge. For requested remote delivery, target the configured development branch (normally `develop`).

## Completion criteria

For a normal task with local-only delivery, completion means:

- requested scope is implemented;
- required tests are updated;
- relevant targeted checks pass;
- the normal project check passes;
- extended verification was used when justified;
- final review is complete;
- documentation was changed only when explicitly requested, except minimal updates to existing `ARCHITECTURE.md` for material architecture changes;
- task changes are locally committed;
- push/PR/merge were not performed without explicit user authorization.

When the caller defines an earlier or later explicit stop point, complete only those authorized stages and report remaining delivery as pending.

Keep the final report concise; report results/status, not a chronological tool log.
