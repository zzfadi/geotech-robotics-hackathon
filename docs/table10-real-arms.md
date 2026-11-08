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
