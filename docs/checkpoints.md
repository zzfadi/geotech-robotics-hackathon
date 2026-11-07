# Unreal checkpoints

The user selected **15 minutes plus completed milestones**, including GitHub pushes. Luna2 owns the repeating task `TheHumanTouch-Checkpoint`; other computers must not install competing checkpoint jobs. The versioned settings are in `Unreal/TheHumanTouch/Config/CheckpointPolicy.json`.

From the Unreal project folder:

```sh
uv run --script Tools/checkpoint.py --dry-run
uv run --script Tools/checkpoint.py
```

The job checks main, the Git index, remote divergence, agent activity and the host's actual session. It defers active tasks/animations and unshared dirty content, persists through CoPresenceFab, verifies the result, commits only changed Content `.uasset`/`.umap` files, pushes main, and verifies the remote hash. No changes means no empty commit. Source/config edits are reviewed and committed by the working agent; the checkpoint job may push already committed main changes. A failed push preserves its commit and retries on the next run. It does not pull binary assets into a live editor.

Status and logs live under ignored `Saved/Checkpoints/`. A `deferred` status needs attention; elapsed time is not a successful checkpoint. Save dialogs, busy agents, dirty content, authentication errors or remote changes stop the operation safely. Keep scheduled-task overlapping execution disabled; the script also locks across processes. The first run is due 15 minutes after installation, then every 15 minutes while Luna2's user is logged in. Milestone runs use the same command and lock.

Install/update the host schedule with `Tools/install_checkpoint_task.ps1`; it calls `Tools/run_checkpoint.ps1` in the interactive user's context. Git credentials must have write access to this repository. The job checks write access with a push dry-run even on unchanged runs and never opens unattended credential prompts. Verified November 7: the schedule is installed. After the user authorized the same account as the Mac, Luna2 signed in as `Batname` through Git Credential Manager device login and its unattended push dry-run succeeded. The repository-local username selects that account; credentials remain in Windows credential storage. The earlier Geotech-Agents permission blocker is resolved by this account change.

## Keeping transaction history short

Persisting produces durable project files; a Git commit records those files. Neither truncates an active Concert session. Avoid redundant property writes and group related edits into bounded scoped transactions. At a successful checkpoint, a `session_rollover_required` flag indicates a new baseline is ready; it is **not** an automatic disconnect or claim that the history was compacted.

At the next coordinated pause:

1. Finish active tasks and preserve each participant's local/unshared work. Run the host checkpoint and confirm the pushed hash.
2. Archive the current session through Multi-User Browser/Server and verify the archive exists. Keep it for recovery.
3. All participants leave the old session. Synchronize the same pushed main revision while editors are safely closed or disconnected and not holding modified assets.
4. Create a fresh session (a new session ID, even if reusing `TheHumanTouch` after retiring the old live session), and reconnect the single editor on each machine. If using a new display name, update the launcher and checkpoint policy together.
5. Verify participant membership, the saved level and agent replies. Do not restore the old archive into the new session, because that carries its transactions forward.

Never remove `Saved/MultiUser` or a live database to clear history. Keep the existing session running while participants are working; report rollover as pending until coordinated.

Epic references: [persistence and session behavior](https://dev.epicgames.com/documentation/unreal-engine/multi-user-editing-overview-for-unreal-engine), [session archives](https://dev.epicgames.com/documentation/unreal-engine/saving-and-restoring-session-archives-in-unreal-engine).

## UE 5.8 map acknowledgement fix (prepared)

The YAM milestone exposed two false blockers. A completed hub task retains its title; idle detection now uses the terminal state while keeping editor task/animation checks. The engine's live-transaction query joins save records to activities, excluding the local dummy save event generated during persistence. Read-only inspection on Luna2 found map event 54 saved/persisted through transaction 6, equal to the newest transaction, while the public pending API still returned Empty. No database records were modified.

The native helper now optionally publishes loaded pending maps as ordinary map saves inside its existing explicit persistence guard. The host checkpoint waits for the server acknowledgement and may re-persist up to three times without republishing the map. Dirty or active editor state still fails the operation, and unresolved pending packages time out. The ordinary save guard remains unchanged. Four regression tests cover acknowledgements, bounded retries, active/dirty refusal and timeout.

A Win64 BuildPlugin build succeeded with this fix and an explicit AssetTools map-rename helper. Deployment requires one coordinated host-editor restart; the running server/session stays intact. Runtime verification and final binary checkpoint remain pending. The user additionally requested renaming Empty to TheHumanTouch and setting both project default maps; the host rename lifecycle is prepared in Tools/rename_human_touch_map.py.
