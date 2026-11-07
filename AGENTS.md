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

The team dashboard at https://zzfadi.github.io/geotech-robotics-hackathon/ is how everyone tracks what changed, where and why. Everyone posts there.

- After each meaningful change, decision or blocker, add one new file to `dashboard/updates/` named `YYYY-MM-DD-HHMM-<short-title>.json` (Pacific time, lowercase). Include it in the same commit as the work when you can.
- Required fields: `when` (ISO time with offset), `workstream` (an id from `dashboard/data/workstreams.json`), `status` (`done`, `in-progress`, `blocked`, `decision` or `note`), `what` and `why`. Optional: `where` (paths, commit SHAs, `#PR` or URLs), `details` and `workstream_update` (`milestones_done` with milestone ids, `status`, `owner`, or `progress` 0-100).
- Finished a milestone? List its id in `workstream_update.milestones_done`; that is what moves the track's progress. Milestones, owners and track `paths` live in `dashboard/data/workstreams.json`.
- Don't name yourself in the entry. The dashboard credits every update and commit to the GitHub username that pushed it to `main`.
- Run `python3 dashboard/build.py --check` before committing. Never edit or delete another contributor's update file.
- Full format and an example: [dashboard/README.md](dashboard/README.md).

## Working practices

### Checkpoints, GitHub pushes and Multi-User history

- Checkpoint the Unreal project **every 15 minutes and after completed milestones**, and push successful checkpoints to `origin/main` every 15 minutes. This standing request authorizes routine checkpoint commits/pushes; do not repeatedly ask permission.
- Luna2 is the single automatic checkpoint writer. Run `Unreal/TheHumanTouch/Tools/checkpoint.py` through its 15-minute scheduled task; agents may invoke it at a milestone. Read `Config/CheckpointPolicy.json` and `docs/checkpoints.md` first.
- Finish active edits and share dirty content before persistence. Do not checkpoint a partially completed agent task. Persist via the guarded CoPresence host path and verify no dirty/pending packages remain before committing.
- Automatic commits include only Unreal `.uasset`/`.umap` files under the project's Content directory. Review and commit source/config/documentation changes as focused agent commits and push these regularly too. Never stage unrelated changes or secrets.
- Keep transactions small in number: batch related edits into a meaningful scoped transaction; avoid per-frame edits, redundant writes, and repeated no-op setters. Keep a logical task undoable; do not make one huge unbounded transaction.
- Git commits and persistence do **not** clear Concert history. At completed checkpoint boundaries, coordinate a fresh session: archive the old session, ensure all participants have the pushed checkpoint and preserved local work, leave the old session, and join a new session from that identical baseline. Retain the archive for recovery. Never delete live transaction databases, reset undo to simulate compaction, or restore the whole archive into the new session.
- If people are actively editing, defer session rollover until a coordinated pause; report it as pending. Do not disconnect participants automatically. Never claim a push alone reduced the active transaction history.
- Check `Saved/Checkpoints/status.json`; `deferred` means incomplete, and failed pushes must retain local commits for retry. Resolve divergence carefully on main; never force-push or discard work. An unchanged project needs no empty commit.

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

### Maker Arm robot description

- Target MakerMods **Maker Arm**, not Metal Arm or the earlier I2RT YAM preview. Official sources: https://www.makermods.ai/maker-arm and https://github.com/makermods-robotics/maker-arm-sdk/tree/main/urdf/maker_arm.
- URDF means Unified Robot Description Format. Use `Unreal/TheHumanTouch/SourceAssets/MakerArm/robot.urdf` and its adjacent `meshes/`. The pinned upstream commit, license and SHA-256 hashes are recorded in `provenance.json`; preserve upstream files and provenance when making derived assets. Review upstream changes before updating the pin.
- Read `docs/maker-arm-model.md` and the source model README before changing geometry, kinematics or physics. Use `maker_arm_model.py`, `import_maker_arm_meshes.py` and `maker_arm_station.py` for the existing Unreal representation; do not substitute YAM joint offsets or limits.
- Preserve the six revolute arm joints, symmetric prismatic jaw mimic and fixed `grasp_center`. The pose helper takes six URDF radians and total jaw gap in metres (0–0.104825); SDK motor coordinates require a separately calibrated mapping.
- Preserve CAD frames and mesh scale; the existing conversion handles Y-up to Z-up, metres to Unreal centimetres and handedness. Do not apply an additional display rotation or scale without checking the resulting geometry.
- Describe the current Unreal model as an editor kinematic representation. Dynamics, collision/contact behavior, hardware alignment and gripper transmission remain unvalidated; importing meshes does not establish a calibrated digital twin.
- After model/conversion changes, run `python3 -m unittest discover -s Unreal/TheHumanTouch/Tools -p 'test_maker_arm_model.py'` and verify source hashes/mesh references. Follow the scoped editing and checkpoint rules for live Unreal changes.
