# Agent instructions

## Branch workflow

- Always work on `main`. Make and commit changes directly on `main`; do not create feature branches, detached checkouts, or branch-based worktrees.
- Before editing, inspect `git status --short --branch` and confirm the current branch with `git branch --show-current`. If it is not `main`, preserve existing work before switching to `main`.
- When `origin/main` exists, fetch and synchronize with it before starting work. Use `git pull --ff-only origin main` when the working tree is clean; never discard local changes to synchronize.
- Keep commits focused. Stage only files belonging to the task, review the staged diff, and run checks appropriate to the change before committing.
- Push authorized changes to `origin main`. Never force-push, rewrite shared history, or bypass branch protections. If repository policy prevents direct pushes, report the blocker.
- If `main` advances before a push, fetch it and integrate its changes on `main`, resolving conflicts carefully and repeating affected checks before retrying the push.

## Conflict resolution and deferred work

The [Slack recommendation from November 5, 2026](https://geotech-org.slack.com/archives/D0CESCYB1SL/p1793937372838249) parks the Smart Links agent-instructions change and proposes a merge-conflict resolution agent as future agentsync work after the proof of concept.

- Keep the Smart Links-specific instructions change deferred unless explicitly requested.
- Treat a dedicated merge-conflict resolution agent as future work, not an existing capability or an instruction to build or delegate to one.
- For conflicts encountered now, inspect both sides and preserve the intended behavior of each change. Do not blindly select one side or overwrite another contributor's work.
- If the correct resolution depends on an unclear product decision, explain the conflict and ask for that decision. Otherwise resolve it, verify the result, and continue on `main`.

## Working practices

- Read applicable repository instructions before changing files.
- Preserve unrelated user changes and never commit credentials, tokens, private keys, or local secret files.
- Keep documentation aligned with implemented behavior. Report what changed, what was verified, and any remaining blockers.
