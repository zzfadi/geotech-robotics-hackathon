# Table 10 ↔ real Maker Arms

Table 10 (`MakerInteraction_T09__MakerArm_*`, x ≈ 2480 cm) is the station linked to the two physical Maker Arms. The plain `MakerArm_*` actors belong to fleet Table 1; don't link or pose them.

## What runs, and where

| Piece | Where | Role |
|---|---|---|
| `DreamSim/table10_agent.py` | Mac, login agent `one.geotech.table10-agent` | While the editor is up, keeps the editor link, the live Table 10 camera and the real-arm service running. Relays the web buttons and sends the drive heartbeat. |
| `Unreal/TheHumanTouch/Tools/maker_arm_real_link.py` | Editor (started by the agent) | Mirror: poses Table 10 from the measured real joints. Drive: publishes Table 10 joints, from tests or from grippers dragged in Unreal or VR. |
| `Unreal/TheHumanTouch/Tools/real_arm_service.py` | Mac, maker-arm-sdk venv (started by the agent) | Headless `real_arm_bridge.Bridge` that holds both USB-CAN adapters. It starts in mirror (torque off). |
| `Unreal/TheHumanTouch/Tools/maker_arm_table10_camera.py` | Editor | Live overhead stream, uploaded to the web page. |
| Web page, Table 10 panel | Luna2 debugger | Live camera, link and sync status, Enable drive / Torque off, and the Move up / Go to idle tests. |

Files exchanged under `Unreal/TheHumanTouch/Saved/RealArmLink/`:

| File | Direction |
|---|---|
| `real_state.json` | service → editor |
| `ue_state.json` | editor → service |
| `command.json` | web tests |
| `control.json` | drive heartbeat |
| `service.json` / `service.log` | service status |

Install or restart the login agent with `zsh DreamSim/install_table10_agent.sh`. Remove it with `... remove`. The log is `~/Library/Logs/CoPresence/one.geotech.table10-agent.log`.

Requirements:
- The local `maker-arm-sdk` clone with the gs_usb patch (see the Maker Arm SDK handoff).
- Adapters: left `003A003F4146570320303038`, right `0027002E4146570520313238`.
- No other program holding the adapters (MakerModsLab, or a terminal `real_arm_bridge.py`).

## Modes and tests

- **Mirror** (default, torque off): move a real arm by hand and Table 10 follows. The test buttons play a virtual preview, then Table 10 eases back to the real pose.
- **Drive** (Enable drive in the web panel): the real arms follow Table 10.
  - **Move up** raises both grippers 8 cm straight up.
  - **Go to idle** returns both arms to the folded rest, `[0, −6, 6, 0, 0, 0]°`. The fleet idle pose is outside the real joint-4 range, so it can't be used.
  - Grippers dragged in Unreal or VR also move the real arms.
- Test motions run at most 0.35 rad/s, inside the motor limits.
- Verified 2026-11-08:
  - Move up: the real left arm's joint 2 went −6 → −23° and joint 3 6 → 31°, staying within 3.5° of Unreal throughout.
  - Go to idle: the arms returned to within 1–3°.

## Safety

These sit on top of the bridge's own guards:
- Drive engages only when Unreal matches the real arms within 5°.
- Joint speed is capped at 0.6 rad/s.
- A target jumping more than 25° is rejected.
- Unreal state older than 0.3 s means the arm holds its last target.
- Torque is released only after a verified return to the engage pose.

Added for unattended use:
- **Dead-man.** Drive needs a fresh heartbeat. The agent writes it only while the web server answers. If the heartbeat is lost, drive is switched off, or Unreal state is missing for more than 5 s, the arms return to their engage pose and torque is released. If that return can't be verified, the SDK keeps holding; **Release hold** in the panel releases once the arm is supported.
- **Expiry.** Drive switches itself off after 15 minutes.
- **Hitches.** An editor hitch pauses a test instead of making it jump. If the real arm stays more than 12° behind Unreal for 1 s, the test aborts.
- **Access.**
  - Drive can only be switched on from the tailnet, never through the public (Funnel) link, even with the operator code.
  - Torque off and Stop work for everyone.
  - Public visitors are view-only unless they have the operator code, which is kept in `Saved/DreamDebugger/operator-code` on Luna2.
- Joints 1–6 have no motor watchdog in this SDK: keep the hardware E-stop within reach whenever drive is on.

## Lessons from the first hardware sessions (2026-11-08)

- **Turning off must never wait.** The terminal bridge's release blocked on a typed RELEASE after a fault, so the web
  Torque off did nothing and the status froze. The service now never blocks.
  - An arm that can't verify its return is shown as **holding**. **Release now** turns torque off immediately, via the
    SDK's thread-safe `estop()`.
  - A faulted arm still at its engage pose is released automatically.
- **Power off is torque off.** When no motor answers for 2 s, the state becomes **off**, drive is dropped, and the
  service reconnects by itself when power returns.
  - The Turn arms ON button explains why it's unavailable (for example, "release the E-stops").
  - Turn arms OFF is always clickable.
- **Motors dropped off the CAN bus right after torque on.** Every left-arm motor stopped answering within 0.85 s of
  enable, then the right elbow and wrist reported "feedback timeout — check bus/power". With torque off, the same
  motors answer normally.
  - Suspects: the arm power supply sagging under torque, or the CAN cabling and termination.
  - In place now:
    - drive runs at 25 Hz;
    - every motor must pass a clean-read check before torque on;
    - CAN error and echo counters plus feedback age are logged every second;
    - drive can be switched on per arm (Left / Right) to isolate a weak arm.
- **No phantom moves.** While an arm is faulted or holding, test buttons are refused instead of moving only the
  virtual Table 10.

### Hardware finding (2026-11-08, 13:0x)

With torque off, both arms answer cleanly: 0 CAN error frames at about 246 frames/s per arm.

The test plan was right arm alone, then left, then both. Each time torque came on, motors dropped out within
about 0.3 s:
- **Right arm (at 25 and at 50 Hz):** elbow_flex, then wrist_flex.
- **Left arm (earlier):** every motor.

The pattern:
- no CAN errors during drive;
- a burst of error frames right after release (motors rejoining the bus);
- no latched fault bits.

This looks like a power connection that holds at idle current and sags under motor load, at the right elbow chain
and the left base cable or supply.

The software now handles it safely: the faulted arm is released at its start pose and the page returns to ready.
Driving needs the power and CAN chain of both arms checked first.

## VR cup handover with Dream (editor world)

In VR, Denys places `MakerInteraction_T09__HandoverCup` on Table 10 and says "take the cup". The VR session's voice
trigger writes `Saved/Table10Dream/request.json`.

`DreamSim/table10_dream.py` runs through `DreamSim/start_table10_dream.command`, which injects the Dream key via
op-agents. It reports progress in `status.json` (queued, starting, running, done or failed).

The pickup has three steps:
1. a planned reach with the nearer arm that can reach the cup;
2. a Dream grasp, with the arm held still;
3. a Dream lift, applying only Dream's upward motion, to about 10 cm.

A prompt containing put, release, drop, reset or let go sets the cup down upright and returns both arms to idle.

Motion and cameras run in the editor world, the one VR shows, through `Tools/maker_arm_table10_dream.py`. The cup
is carried kinematically while a jaw squeezes it ("editor-world kinematic carry"). The real-arm link recovers those
poses, so the real arms follow only when the real-arm service is in drive.

Tested 2026-11-08:
- Pickup: the left arm held the cup 12.7 cm up, at 4.4° tilt, after 1 grasp and 2 lift calls.
- Put-down: the cup was set down upright and both arms returned to idle.

Reach: along the centre line (y = 0) each arm reaches x 30–50 cm only. Off-centre, each arm covers most of its half.

### Beer can, and a duplicate-instance bug (2026-11-08, 13:5x)

Table 10 now hands over `MakerInteraction_T09__HandoverCan`, an Engine cylinder 6.6 × 12.2 cm, spawned by the VR
session. The runtime looks up the can or the cup by label on every tick and handles the can's straight wall.
Test: the left arm held the can 10.6 cm up at 1.4° tilt; put-down passed.

**Bug found and fixed.** Every editor reload-then-start of the link or of the Table 10 runtime left the old
instance ticking. One sweep found 5 link instances and 2 runtimes running together.
- Each link kept its own arm state and published `ue_state.json` in turn. In drive, the bridge would have received
  alternating joint targets: a plausible contributor to the motor dropouts under torque.
- Both modules' `start()` now stops stray instances of themselves, including ones orphaned by a reload.
- Re-test the real arms with a single link before concluding it is only hardware.
