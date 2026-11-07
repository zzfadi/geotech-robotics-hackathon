# Opt-in runtime physics controller

**Integration is not yet peer-safe or accepted.** The initial local lifecycle smoke passed, but Luna2 later crashed while replaying leaked PIE capture deletions from the open editor mirror transaction. Runtime commands are paused pending a connected-peer lifecycle retest.

`maker_arm_runtime.py` is a single-authority Simulate-in-Editor controller. It is separate from ordinary editor kinematic playback. A Dream action still passes the pinned URDF joint/aperture validation and velocity retiming. Its joints drive official visual link transforms; free table blocks move through Chaos physics. The controller never attaches a block to the gripper or writes a block lift trajectory.

## Current approximation

Four hidden runtime collision proxies use the existing coarse convex jaw meshes (left 40 hulls, right 50). Official visual geometry remains visible. These collision shapes and the assumed 0.05 kg block mass / 0.8 friction are not calibrated one-to-one dynamics. Other arm links retain their current collision configuration. The passing isolated jaw fixture is evidence for this collision path, not autonomous Dream pickup or hardware fidelity.

## Lifecycle

- `start(seed_path, status_path, camera_directory, mirror=True)` validates the initial editor pose, creates local temporary collision/capture actors, starts SIE, enables prop physics, starts runtime cameras, and enters `ready`.
- `launch(report_path, playback_path, hz=20)` validates a fresh runtime observation and applies the bounded Dream joint chunk. The output preserves the worker's `done`, `error`, state and timing schema.
- `stop(commit=True)` ends the measured-transform mirror and SIE, verifies/reseeds editor feedback, removes temporary local actors and resumes the editor camera stream. `commit=False` restores original editor transforms rather than retaining runtime measurements.

Temporary actors are tagged local-only and created outside shared transactions. Unreal Python cannot spawn these actors directly in the runtime world, so SIE clones local editor actors and the runtime controller binds those copies. The original visual assets are not replaced. Camera packets explicitly mark `scope: runtime` or `scope: editor`; inference must await fresh runtime packets in this mode.

The mirror uses the cached pre-SIE editor world because `get_editor_world()` returns None during SIE in this installation. It copies measured runtime transforms into editor counterparts at 10 Hz, using scoped editor transactions, and flushes the final sample at commit. The original long-lived transaction has been rejected following the peer crash; short per-sample transactions are implemented and pass offline lifecycle tests, with connected-peer validation pending. Peers display those measurements; they do not independently simulate physics. The controller stops if the mirror closes, a concurrent edit invalidates ownership, the world changes, a stop file appears, or 170 seconds elapse. Mirror and camera bounds are 180 seconds.

Status includes per-prop initial, maximum and final heights, but always reports `task_success_verified: false`. A rising block alone can be a collision ejection; successful pickup needs task-relevant contact and stable object motion, not merely successful API/chunk completion.

## Integrated smoke

The live Mac smoke started SIE, captured all three runtime views, validated runtime feedback against every arm link, launched a three-sample 0.1-second bounded joint trajectory through the same MCP/deferred callback path used by the worker while the mirror transaction was active, then aborted. SIE ended, the original editor pose was verified restored, and all three editor camera views resumed (0.124-second frame age). This verified only the local control lifecycle; it did not prove peer-safe transaction teardown or autonomous pickup.

A second no-motion commit smoke completed with no error, all three blocks at their initial supported heights, editor feedback verified, and no remaining temporary collision proxies. Compact evidence is in `docs/validation/maker-arm-runtime-smoke.json`. Peer replication of this runtime controller still requires a coordinated command audit.

## Peer crash and teardown correction

Luna2's log at 2026-11-07 22:33:06 UTC reported `EditorServer.cpp:1186`, asserting a valid `CurrentUndoRedoContext` outer operation/depth. Immediately preceding Concert activities 478/479 from the Mac deleted a SceneCapture2D draw-frustum component and other objects in `/Game/Maps/UEDPIE_0_TheHumanTouch`. The runtime observer had destroyed PIE capture actors while the mirror's editor transaction remained open. Those deletions could therefore be serialized to peers.

The controller now closes the mirror before stopping its observer or requesting SIE teardown. Runtime observer shutdown only unregisters capture callbacks; it never destroys runtime actors, including error cleanup. SIE owns their destruction after editor transactions close. Three offline regression tests exercise normal/error teardown order and the absence of runtime capture deletion. The mirror now confines transactions to each measured sample, so no editor transaction survives a Slate tick. Twelve mirror tests and three teardown-order tests pass offline. Connected-peer validation is still pending.

Acceptance still requires a connected-peer start, measured motion, abort/commit stop and fresh camera recovery, with no PIE-package transaction leakage. The previous smoke evidence remains historical and explicitly does not establish that acceptance.
