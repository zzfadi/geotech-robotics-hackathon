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

## UE 5.8 map acknowledgement fix (deployed; verification incomplete)

The YAM milestone exposed two false blockers. A completed hub task retains its title; idle detection now uses the terminal state while keeping editor task/animation checks. The engine's live-transaction query joins save records to activities, excluding the local dummy save event generated during persistence. Read-only inspection on Luna2 found map event 54 saved/persisted through transaction 6, equal to the newest transaction, while the public pending API still returned Empty. No database records were modified.

The native helper now optionally publishes loaded pending maps as ordinary map saves inside its existing explicit persistence guard. The host checkpoint waits for the server acknowledgement and may re-persist up to three times without republishing the map. Dirty or active editor state still fails the operation, and unresolved pending packages time out. The ordinary save guard remains unchanged. Four regression tests cover acknowledgements, bounded retries, active/dirty refusal and timeout.

A Win64 BuildPlugin build succeeded with this fix and an explicit AssetTools map-rename helper. The host editor was gracefully restarted after checking a recovery archive against every saved content file; its replacement PID is 31272 and the native rename API was verified. The server/session was retained. Final persistence still fails: ordinary map publication creates new PersistentLevel-only transactions from Mac and Brian VR, each with zero summarized actions. A diagnostic observes no dirty map immediately after persistence, then a dirty map by 0.5 seconds. No database edits, undo resets, or dirty-flag clearing were used. Final binary checkpoint remains pending. The user additionally requested renaming Empty to TheHumanTouch and setting both project default maps; the host rename lifecycle is prepared in Tools/rename_human_touch_map.py.

The rename completed through AssetTools after its confirmation dialog was accepted. Both project defaults now target `/Game/Maps/TheHumanTouch`, and the Mac and Luna2 loaded the renamed map. The remaining `Empty` redirector caused a Mac Concert reload crash; Unreal's Update Redirector References operation removed it after verifying that no references remained. The removal replicated to the Mac. Map reload subsequently completed without that crash, but the dirty-map guard still refused the checkpoint. Recovery `.umap` and text exports remain under ignored `Saved/CheckpointRecovery/`.

### Transient local presence transaction fix

Reload logs showed `EditorActorSubsystem.spawn_actor_from_class(..., transient=True)` recreating the local self marker through the editor actor factory, followed by PersistentLevel-only Concert transactions. Engine source confirms this path starts editor placement transactions even for transient actors. The new native `SpawnLocalPresence` uses `UWorld::SpawnActor` with transient/nontransactional flags and a unique local-only name. It refuses to run inside an active transaction, restricts the class to the transient CoPresence ghost, and never clears a package dirty flag. Duplicate-marker cleanup uses the equivalent restricted runtime destruction path. Python label updates pass `mark_dirty=False`.

Older native builds omit only the local self marker when the new API is missing; remote Concert avatars remain available. Every connected editor that runs local presence must receive the fix before claiming the reload loop resolved. A successful native build is not proof of a clean live checkpoint. Continue enforcing the existing task, dirty-content, dirty-map and pending-package checks.

Validation: Win64 BuildPlugin succeeded; the Mac arm64 plugin compiled successfully with local compile actions after UBA initially exited 133. Three local-presence regression tests and 13 checkpoint tests passed. Native binaries are staged under ignored Saved/Build paths, not yet installed into running editors.

Live verification at 20:58 UTC: the compatible Python change was reloaded on Mac and Luna2 without swapping native binaries. The next normal publication reloaded the Mac map without the old ActorFactory spawn or an empty Mac transaction. Only Brian VR produced the new empty transaction (activity 244), and the guard again deferred persistence. Brian's editor still needs the matching update; its session endpoint at this observation was `9D71A025654337CEEFDB9CAF179EEAF5`. Full native deployment and successful binary persistence are not yet verified.

The user confirmed that Brian’s editor is currently unavailable. Leave that participant connected and preserve the session; do not bypass the checkpoint guard or claim the binary level has been pushed. Robot implementation can continue independently.

### Verified successful checkpoints, 21:18–21:21 UTC

Luna2 status now reports `pushed` for `a2c77b28f355d2aac3ddff2321a33d4617da5f88` (imported robot assets, experimental collisions, table materials and renamed map) and then `21e6d4efdc6534ac3c814bf525160374304bff2c` (the latest map with the restored small mast camera body). Both commits were fetched from GitHub; only TheHumanTouch remains in the committed Maps directory. The previous dirty-map blocker did not prevent these guarded runs. Mac and Luna2 remain connected with Brian VR. Session rollover remains pending and was not performed automatically.

The Mac editor remains open. Its source commits were integrated with the host's binary checkpoint in a separate checkout on `main` at `/tmp/humantouch-main-integration-1320`, avoiding a binary file replacement under the live editor. Its original live checkout still needs synchronization after preserving work and closing safely. No success claim relies on hot-copying Content or clearing dirty flags.
