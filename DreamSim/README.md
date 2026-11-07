# Dream → Maker Arm Unreal integration

Verified November 7, 2026: `dreamlabs/dream-1-beta-flash` accepted the three Unreal camera images and measured CAD joint state for “Move the left gripper toward the red cube on the table.” It returned two 48 × 7 trajectories in 2.73 seconds. Both were played through all 48 rows in the live shared Unreal scene in one editor transaction, then the starting pose was restored. This proves text-to-kinematic-motion integration, **not** a successful grasp, continuous feedback controller, physics articulation, or voice control. Those remain in progress. The web debugger subsequently verified two successive feedback cycles; see below.

## Exact arm convention

`model.py` derives a custom Dream arm file from the pinned upstream URDF. It preserves all source axes, origins and limits and adds the same Y-up mounting conversion used in Unreal. The built-in `makers` profile differs in one wrist axis and several joint limits; do not substitute it. The CAD grasp center is used as the simulation TCP. This is not physical TCP calibration, and no human hardware-calibration record is fabricated. The SDK's physical calibration/input-review warnings remain visible.

Joint state and returned joint targets are six URDF radians per arm. Gripper input is total jaw opening in metres; Dream uses normalized opening (0–1) over 0.104825 m. The policy uses the **EEF head** and custom IK, not YAM joint predictions. The explicit embodiment config declares three camera roles and both arms' EEF state.

## Probe

Dependencies: Python 3.12, `dreamlabs-client==1.1.3`, `dreamlabs==1.0.37`. Inject `DREAM_API_KEY` from the authorized credential store into the process environment; never put the key in scripts, command arguments or version control. Set `DREAM_LOG=off` for the probe.

```sh
uv run --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 python DreamSim/probe.py \
  --state /path/to/verified-state.json \
  --overhead /path/to/overhead.jpg --left /path/to/wrist_l.jpg --right /path/to/wrist_r.jpg \
  --prompt 'Move the left gripper toward the red cube on the table.' \
  --output /path/to/local-result.json
```

State contains `q_left`, `q_right` (six radians each), `gap_left_m`, `gap_right_m`. Images must correspond to that state. The probe resizes JPEGs to 320 × 176 and requests one action chunk; it never moves anything. Capture images, results and credentials stay outside the repository. It sends a real single-frame observation, not a fabricated timestamp history.

## Unreal helpers

- `Tools/maker_arm_observer.py`: local-only SceneCapture2D actors follow the existing cameras, writing an atomic packet containing all three JPEGs and capture timestamps. An eight-second test produced 39 packets at about 5 Hz. Captures expired and all temporary actors were verified removed. The editor viewpoint does not move. It currently captures images only; continuous control still needs joint state synchronized with each packet.
- `Tools/maker_arm_actions.py`: rejects malformed, nonfinite, mismatched, over-speed or out-of-limit chunks. Only numerical limit overshoot up to 0.0001 rad is clamped; the actual first reply needed corrections below 0.000043 rad.
- `Tools/maker_arm_playback.py`: bounded editor kinematic preview, one transaction for the entire chunk. Validates the observed pose against actual actors before starting. Start after the enclosing CoPresence transaction ends, within an active task. `stop()` ends at the current pose. This is not a runtime controller; it does not simulate object contacts, smooth the initial grip transition, or establish physical safety. A map reload invalidates cached actor references and terminates playback.

Checks: `python3 -m unittest discover -s DreamSim -p 'test_*.py'` plus the Unreal Tools tests `test_maker_arm_actions.py` and `test_maker_arm_model.py`.

Official API source: https://github.com/dreamlabs-co/dreamlabs and https://platform.dreamlabs.ai/docs/control-loop/ .

## Luna2 web debugger and prompt control

Open **https://luna2.tail6864ac.ts.net/** on the tailnet (the original HTTP address also remains available). The page provides a prompt box, Run in Unreal and Stop controls, three camera previews, request timing/activity, joint trajectories and the latest verified state. The page is hosted on Luna2 by scheduled task `TheHumanTouch-DreamDebugger`; the policy/Unreal worker currently runs on the Mac's shared editor. Keep that editor and worker running. A grey worker badge disables submission.

The November 7 browser test submitted “Move the left gripper toward the red cube on the table.” through the actual Run button (request `ef3ae184153440c3a6870bc763ea68f1`). Two model requests took 3.74 s and 0.53 s. Both 48-step trajectories completed, with fresh camera/joint-state feedback between them. The captured overhead video shows the left gripper moving above the red cube. It does **not** establish that a cube was grasped. The 64-frame, 12.8-second H.264 clip fully decoded successfully and its initial/middle/final frames were reviewed. Captures and logs live under ignored `Saved/DreamDebugger/worker/recordings/<request-id>/`.

`debug_server.py` uses only Python's standard library. `install_debugger.ps1` copies its runtime into Saved and installs the Luna2 scheduled task. It binds the tailnet IP only; the page is available to authorized tailnet peers. Browser commands require JSON and same-origin requests. Worker event and queue endpoints require a separate bearer token stored under Saved; it is never returned to the browser. Dream credentials remain in the worker environment. No public port forwarding was added.

`start_worker.command` starts the Mac worker using the existing Agents Vault helper. Copy the server's `Saved/DreamDebugger/worker-token` privately to the Mac's corresponding Saved directory (already configured on these two machines). The initial zero-pose seed is checked against every actual link; it is rejected if the scene differs. Each later observation validates the tracked kinematic state against every current actor transform. An external edit or missing camera stops the loop instead of publishing invented state.

A command currently runs a bounded series of three feedback cycles (the verification run used two). Stop interrupts active playback and discards a response still in flight. Each movement chunk is one shared editor transaction. The loop holds position during inference and is not yet a continuous physics controller. `Record voice` captures up to 29 seconds through the browser microphone on the HTTPS page. `Audio file` accepts an existing recording. Both use local `faster-whisper==1.2.1` with the `base.en` model in the Mac worker; `av==17.0.0` is pinned because version 19 removed a decoder argument used by faster-whisper. Audio stays inside the tailnet and worker, and is removed from the server queue after transcription. The transcript fills the prompt for review before Run in Unreal. Live microphone input and object-contact physics still require verification.

Recording source frames and the playable clip are retained locally; no API keys, worker tokens, images or request logs are committed. Run `python3 -m unittest discover -s DreamSim -p 'test_*.py'` for model/frame and debugger authentication, queue, duplicate-command and cross-origin checks.

### HTTPS

Luna2 Tailscale Serve provides the private HTTPS endpoint and a valid certificate, forwarding to the server's additional loopback listener `127.0.0.1:8788`. No public Funnel is enabled. Existing Serve configuration was empty before this route was installed. The configuration is `tailscale serve --bg --https=443 http://127.0.0.1:8788`; inspect `tailscale serve status --json` before changing it so other services are preserved. TLS and the browser page were verified from the Mac.

### Recorded-voice verification

A synthetic macOS `say` clip saying “Move the right robot arm toward the orange cube.” was uploaded using the HTTPS page's Audio file control. The local worker returned the exact transcript (voice id `53d49507165f4ae183639d85ac753d01`). Clicking Run submitted it with `source: voice` (command `dc0407af20ed44388299468dcb9ca60b`). Both 48-step feedback cycles completed; inference took 2.69 s and 0.39 s. A 57-frame, 11.4-second overhead recording fully decoded and its initial/middle/final frames were reviewed, showing right-arm motion. This verifies recorded speech → transcription → Dream → Unreal movement, not a live microphone test or successful cube pickup. Local transcription follows the [faster-whisper API](https://github.com/SYSTRAN/faster-whisper).
