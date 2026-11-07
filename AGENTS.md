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

## Dashboard updates

The team dashboard at https://zzfadi.github.io/geotech-robotics-hackathon/ is how everyone tracks what changed, where and why. Every agent and teammate posts there.

- After each meaningful change, decision or blocker, add one new file to `dashboard/updates/` named `YYYY-MM-DD-HHMM-<who>-<short-title>.json` (Pacific time, lowercase). Include it in the same commit as the work when you can.
- Required fields: `when` (ISO time with offset), `who`, `kind` (`agent` or `human`), `workstream` (an id from `dashboard/data/workstreams.json`), `status` (`done`, `in-progress`, `blocked`, `decision` or `note`), `what` and `why`. Optional: `for`, `where` (paths, commit SHAs, `#PR` or URLs), `details` and `workstream_update` (`status`, `progress` 0-100, `owner`).
- Run `python3 dashboard/build.py --check` before committing. Never edit or delete another contributor's update file.
- Full format and an example: [dashboard/README.md](dashboard/README.md).

## Working practices

### Luna2 and agent startup

- Use the installed `luna2-unreal-meet` skill for Luna2 access and lifecycle verification. On Denys's Mac it is at `/Users/geo_dennis/.codex/skills/luna2-unreal-meet/SKILL.md`; read its referenced operating guide too.
- This project's settings override the skill's original AgentCoPresence defaults: UE **5.8.3 CL 58210709**, project `Unreal/TheHumanTouch/TheHumanTouch.uproject`, server/session `TheHumanTouch`, Luna2 `100.84.28.59`, Multi-User UDP `9040`, Agent Chat `8791`, and local editor MCP `8041`.
- Luna2 must run **Claude** as `Denys Luna2 Claude`; the Mac uses **Codex** as `Denys Mac Codex`. Starting only the server or a human editor does not complete agent startup.
- Use `Tools/join_human_touch.py` for the single shared human/agent editor, plus this project's agent runner. Do not use the legacy two-editor join workflow or silently start the original AgentCoPresence project.
- Verify the intended project/session, one editor per machine, the correct brain runner, a visible agent, and an actual Agent Chat reply. Verify voice separately; an open panel or connected helper does not prove audible conversation.
- Keep join codes in ignored local Saved files. Share the current code only for authorized joining; never commit it. See `docs/brian-join.md` for participant setup.

### Unreal session limit

- By default, run only one Unreal Editor instance per computer, including the Mac and Luna2. Human and agent editors count toward the same limit.
- Before launching Unreal, inspect existing editor processes and their project/session. Reuse the existing editor when it is suitable; do not launch a duplicate or a separate companion-agent editor automatically.
- Before switching projects or restarting, preserve unsaved work and close the existing editor gracefully. Never force-close an editor or discard changes just to satisfy this limit.
- Multiple editor instances on one computer require an explicit user request. A Multi-User server is a separate background service and does not count as an editor instance; reuse the intended server rather than creating duplicates.
- After startup, verify the editor count on each requested computer and confirm actual membership in the intended Multi-User session.

- Read applicable repository instructions before changing files.
- Preserve unrelated user changes and never commit credentials, tokens, private keys, or local secret files.
- Keep documentation aligned with implemented behavior. Report what changed, what was verified, and any remaining blockers.
