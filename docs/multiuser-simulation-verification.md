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
