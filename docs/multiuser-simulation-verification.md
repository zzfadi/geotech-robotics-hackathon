# Multi-User simulation verification

## Baseline — 2026-11-07T14:17:58-08:00

Read-only Unreal queries on the Mac and Luna2 reported the same `/Game/Maps/TheHumanTouch.TheHumanTouch` map and `TheHumanTouch` Multi-User session. Both participant lists included Denys Mac Codex, Denys Luna2 Claude and Brian VR. One editor was running on each checked machine (Mac PID 44585; Luna2 PID 31272).

The 23-actor comparison covered 18 arm links, three virtual cameras and the small central camera housing/lens. All actor labels, meshes, scales, visibility, camera FOV, active state and attachments matched. Maximum position difference was 1.42e-14 cm; maximum quaternion-component difference, accounting for equivalent signs, was 3.33e-16. Samples were taken 5.22 seconds apart while no motion task was active. These values establish matching stationary state, not live replication latency.

Both small central camera proxies were visible. All three CameraComponents were active and had no large editor camera mesh. Wrist cameras remained attached to their respective `link_007`; the overhead camera remained attached to the central housing.

Brian VR membership was visible from both peers. His scene contents have not been independently queried; neither Concert membership nor a Git push alone proves his editor has applied every transform. The exposed session/hub data did not supply an independently readable Brian editor endpoint.

## Motion audit

`Tools/multiuser_motion_audit.py` is a read-only bounded sampler. Run `start(<ignored Saved directory>, seconds=60, fps=10)` on each editor before a coordinated command, then compare intermediate and final transforms. It changes no actors, viewport, packages, transactions, or session membership, and removes its callback at expiry. It writes `transforms.jsonl` and completion `status.json`. Default capture is 60 seconds; accepted limits are 120 seconds and 20 Hz.

The coordinated `pick up red cube` request `5773d12046c742619cfd7cc3a19c6502` completed two 49-sample trajectories. Read-only captures covered 77 seconds: 657 Mac samples and 702 Luna2 samples. Each wrist had 95 distinct sampled poses on the Mac and 94 on Luna2, with 92 poses shared after rounding position/quaternion components to 1e-5. Two Luna2 poses were absent from the lower-rate Mac sample set; this is expected from independently timed sampling and is not proof of divergence. This demonstrates intermediate motion replicated to Luna2, rather than only a final pose. It does not measure exact network latency or guarantee every authored row was observed.

After both trajectories ended, all 23 compared actors again matched: maximum position difference 1.42e-14 cm and quaternion-component difference 2.22e-16. The final snapshots were 3.93 seconds apart. Both sampler callbacks were explicitly stopped. Final camera states, attachments and visibility remained unchanged.

Evidence is stored locally under the Mac project's ignored `Saved/MultiUserAudit/command-1421/`: `mac-transforms.jsonl`, `luna-transforms.jsonl`, `mac-final.json`, `luna-final.json` and `comparison.json`. This is kinematic replication evidence, not a successful physical grasp or independently verified Brian VR scene state.

## Persistence boundary

After the coordinated motion task ended, the single Luna2 checkpoint writer persisted and pushed `d8badb2d623fd8c09b3b1eefdb93ec0b810c860f`, containing TheHumanTouch.umap. Its authoritative status is `pushed`, checked at Unix time 1794090148.947486. Source synchronization before persistence was restricted to incoming non-binary changes; the normal active-task, dirty-content and pending-package guards remained enabled. No binary assets were copied into a running editor. Session rollover remains pending; no participant was disconnected and Concert history was not cleared.

## Runtime mirror recovery and abort smoke (2026-11-07, 14:38 Pacific)

Luna2 crashed at 14:33:06 with the Unreal `EditorServer.cpp:1186`
`CurrentUndoRedoContext` assertion and a ConcertSyncClient call stack. Its log
immediately preceded this with activities 478/479: deletion of runtime
`SceneCapture2D` components in `UEDPIE_0_TheHumanTouch`, then discarding that PIE
package. The long-lived mirror transaction included runtime camera destruction;
this is a strong suspected trigger, not a proven engine-level diagnosis.

The original crash report/minidump remain in Luna2 `Saved/Crashes`; a copy of the
crash log and the 14:32 autosaved map are preserved in
`Saved/MultiUserAudit/crash-1433`. The existing `TheHumanTouch-Editor` scheduled
task reopened one editor (PID 12380) and rejoined the original session. No
transaction database was edited, no autosave was discarded, and no other
participant was disconnected by this recovery.

The revised mirror closes each changed sample's editor transaction synchronously,
skips unchanged samples, and restores aborted diagnostic poses in a separate
short transaction. Runtime capture teardown occurs outside those transactions.
This increases bounded transaction history at the configured 10 Hz; it does not
replace the normal checkpoint and coordinated session rollover policy.

A three-sample joint movement followed by abort was observed independently on
Mac and Luna2: each wrist had two distinct poses, both shared across peers at
1e-4 rounding, and all 24 sampled actor positions matched exactly after restore.
The red block remained stationary, as expected for this test. Luna2 survived.
New Concert activities 490/491 modified editor actor components; no runtime
camera-component deletion transaction appeared. A normal PIE-package discard
activity (492) still appeared at SIE exit. This verifies this bounded smoke,
not a model-driven physical pickup or Brian's independently measured scene.

The subsequent commit-path smoke also left all 24 sampled actor positions
identical and Luna2 alive. Activities 493/494 modified editor components, followed
by normal PIE package discard 495; there was no PIE camera deletion transaction.
Fresh 300-second audits in `Saved/MultiUserAudit/runtime-dream-1440` are reserved
for the ensuing model-driven request and include all three free block props.

## Model-driven runtime audit

Request `65854e7f82b44ff2bfcad26bf80c7809` submitted the exact prompt
`pick up red cube`. During the command, Mac recorded 1,121 samples and Luna2
1,151. Each wrist had 283 distinct poses on Mac and 266 on Luna2, with 265
shared at 1e-4 rounding. The red block had five/four distinct sampled transforms,
four shared; green and orange remained stationary. Final positions across all
24 actors matched within 7.1e-15 cm and quaternion components within 2.5e-16.
Independent sample times differ, so intermediate unmatched poses are not by
themselves evidence of divergence.

The red block slid 5.2565 cm horizontally. Its largest sampled Mac height was
77.6179 cm versus an initial 77.5 cm; **this does not verify a pickup**. Replicated
arm motion and small physical object displacement are verified. Both audit
callbacks stopped without error and Luna2 PID 12380 remained alive. The final
Concert activity 782 was a normal PIE package discard; no new runtime camera
deletion transaction appeared in the inspected run.

Machine-readable results: [runtime MU audit](validation/multiuser-runtime-dream.json).
Raw recordings remain in ignored Mac `Saved/MultiUserAudit/runtime-dream-1440`
as `transforms.jsonl`, `luna-transforms.jsonl`, and `comparison.json`. Brian's
membership was confirmed after recovery; his scene was not independently sampled.

## Fleet run on November 7

The mirrored fleet run `20261107-164929-13d280` was sampled read-only on Mac (2 Hz) and Luna2 (5 Hz). Seven tables received joint commands and showed multiple intermediate arm poses on Luna2: T02, T04, T05, T06, T08, T09 and T10. T01, T03 and T07 remained at command sequence 0; their robot motion was not demonstrated. Free-prop physics changes were visible remotely across all ten rooms.

The run stopped at its 170-second duration limit. After teardown, all 260 tracked arm/camera/prop actors on Luna2 matched the Mac pre-SIE baseline within 7.3e-9 cm, with quaternion dot agreement at floating-point precision. Independent near-frame sampling showed up to 0.76 cm position differences during movement; these are not latency measurements. Measured authoritative runtime prop positions were separately compared where snapshots were retained. See `validation/fleet-multiuser-proof-2026-11-07.json` for per-table evidence.

This proves visual-transform replication to Luna2 and restoration, not independent physics on peers, successful pickups on every table, or replication to Brian's unavailable editor.
