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
- `Tools/maker_arm_actions.py`: rejects malformed, nonfinite, mismatched or out-of-limit chunks. Playback uniformly retimes valid paths to the configured joint and jaw speed limits, with a 15-second duration cap. Only numerical limit overshoot up to 0.0001 rad is clamped; the actual first reply needed corrections below 0.000043 rad.
- `Tools/maker_arm_playback.py`: bounded editor kinematic preview, one transaction for the entire chunk. Validates the observed pose against actual actors before starting. Start after the enclosing CoPresence transaction ends, within an active task. `stop()` ends at the current pose. This is not a runtime controller; it does not simulate object contacts or establish physical safety. Retiming includes the observed starting grip, preventing an unbounded first commanded gap change. A map reload invalidates cached actor references and terminates playback.

Checks: `python3 -m unittest discover -s DreamSim -p 'test_*.py'` plus the Unreal Tools tests `test_maker_arm_actions.py` and `test_maker_arm_model.py`.

Official API source: https://github.com/dreamlabs-co/dreamlabs and https://platform.dreamlabs.ai/docs/control-loop/ .

## Luna2 web debugger and prompt control

The worker's opt-in `--runtime-physics` route is under integration. It starts one
authoritative Simulate-in-Editor world per command, requires camera observations
explicitly marked `runtime`, and routes Dream trajectories through the runtime
controller. Official visual meshes remain unchanged; jaw contacts use approximate
convex collision shapes. A controlled fixture has physically retained, lifted,
and released a cube with those shapes; this is not an autonomous Dream pickup.

Runtime commands keep the queue occupied until physics teardown, recording
encoding, and the shared task have finished. Aborts discard runtime playback
seeds so they cannot initialize a restored editor. Episode timestamps and camera
scope checks exclude stale editor frames from inference. Recording writes and
episode resets are synchronized with the capture thread. The default worker
route remains the verified editor kinematic preview until runtime validation
and deployment are complete.

Open **https://luna2.tail6864ac.ts.net/** on the tailnet (the original HTTP address also remains available). The page provides a prompt box, Run in Unreal and Stop controls, three camera previews, request timing/activity, joint trajectories and the latest verified state. The page is hosted on Luna2 by scheduled task `TheHumanTouch-DreamDebugger`; the policy/Unreal worker currently runs on the Mac's shared editor. Keep that editor and worker running. A grey worker badge disables submission.

The November 7 browser test submitted “Move the left gripper toward the red cube on the table.” through the actual Run button (request `ef3ae184153440c3a6870bc763ea68f1`). Two model requests took 3.74 s and 0.53 s. Both 48-step trajectories completed, with fresh camera/joint-state feedback between them. The captured overhead video shows the left gripper moving above the red cube. It does **not** establish that a cube was grasped. The 64-frame, 12.8-second H.264 clip fully decoded successfully and its initial/middle/final frames were reviewed. Captures and logs live under ignored `Saved/DreamDebugger/worker/recordings/<request-id>/`.

`debug_server.py` uses only Python's standard library. `install_debugger.ps1` copies its runtime into Saved and installs the Luna2 scheduled task. It binds the tailnet IP only; the page is available to authorized tailnet peers. Browser commands require JSON and same-origin requests. Worker event and queue endpoints require a separate bearer token stored under Saved; it is never returned to the browser. Dream credentials remain in the worker environment. No public port forwarding was added.

`start_worker.command` starts the Mac worker using the existing Agents Vault helper. Copy the server's `Saved/DreamDebugger/worker-token` privately to the Mac's corresponding Saved directory (already configured on these two machines). The initial zero-pose seed is checked against every actual link; it is rejected if the scene differs. Each later observation validates the tracked kinematic state against every current actor transform. An external edit or missing camera stops the loop instead of publishing invented state.

A command currently runs a bounded series of three feedback cycles (the verification run used two). Stop interrupts active playback and discards a response still in flight. Each movement chunk is one shared editor transaction. The loop holds position during inference and is not yet a continuous physics controller. `Record voice` captures up to 29 seconds through the browser microphone on the HTTPS page. `Audio file` accepts an existing recording. Both use local `faster-whisper==1.2.1` with the `base.en` model in the Mac worker; `av==17.0.0` is pinned because version 19 removed a decoder argument used by faster-whisper. Audio stays inside the tailnet and worker, and is removed from the server queue after transcription. The transcript fills the prompt for review before Run in Unreal. The user completed a live microphone test; the server recorded “Pick up red box.” Object-contact physics still requires verification.

Recording source frames and the playable clip are retained locally; no API keys, worker tokens, images or request logs are committed. Run `python3 -m unittest discover -s DreamSim -p 'test_*.py'` for model/frame and debugger authentication, queue, duplicate-command and cross-origin checks.

### HTTPS

Luna2 Tailscale Serve provides the private HTTPS endpoint and a valid certificate, forwarding to the server's additional loopback listener `127.0.0.1:8788`. No public Funnel is enabled. Existing Serve configuration was empty before this route was installed. The configuration is `tailscale serve --bg --https=443 http://127.0.0.1:8788`; inspect `tailscale serve status --json` before changing it so other services are preserved. TLS and the browser page were verified from the Mac.

### Recorded-voice verification

A synthetic macOS `say` clip saying “Move the right robot arm toward the orange cube.” was uploaded using the HTTPS page's Audio file control. The local worker returned the exact transcript (voice id `53d49507165f4ae183639d85ac753d01`). Clicking Run submitted it with `source: voice` (command `dc0407af20ed44388299468dcb9ca60b`). Both 48-step feedback cycles completed; inference took 2.69 s and 0.39 s. A 57-frame, 11.4-second overhead recording fully decoded and its initial/middle/final frames were reviewed, showing right-arm motion. This verifies recorded speech → transcription → Dream → Unreal movement, not a live microphone test or successful cube pickup. Local transcription follows the [faster-whisper API](https://github.com/SYSTRAN/faster-whisper).

## Playback recovery and timing

A real microphone command transcribed as “Pick up red box.” Its subsequent Dream response exceeded the configured joint speed cap and was rejected before playback. The new retimer preserves ordered targets, prepends the observed pose, and reduces the playback sample rate for both arms together. Offline replay of that saved response produces 49 samples over 8.294 seconds (3.456× time scale), including the initial jaw transition. These are source/model limits, not calibrated hardware capabilities. Live execution of this revised path remains pending.

The worker now waits up to two minutes for an existing editor task, with cancellation, and recovers a candidate seed from the last observation when a rejected playback has no pose. Unreal still verifies every live link before accepting that seed. Playback waits allow the bounded 15-second trajectory plus startup overhead; failures request a stop. Successful grasp and runtime contact control remain unproven.

## Live camera preview and replay

The Luna2 app now polls a separate, latest-frame endpoint twice per second. During each command the Mac worker forwards all three actual Unreal SceneCapture JPEGs on a dedicated thread; inference and observation delivery do not wait for the preview network request. The page marks images LIVE only while their capture timestamp is under two seconds old, otherwise Capture stopped.

Replay last capture and the frame slider inspect up to 300 retained three-camera packets from the most recent command. This RAM buffer is replaced by the next command and cleared by a server restart; it is not a durable video archive. The existing local overhead JPEG sequence and MP4 remain under Saved/DreamDebugger/worker/recordings/<request-id>/. Browser replay was verified with real Unreal camera frames on November 7. The current test commands were rejected for out-of-range grip output, so their preview confirms capture/replay, not successful movement or grasp.

The Dream sandbox was inspected during the user's “take a banana” task: its UI identifies YAM and a 300-second run budget, and the right gripper is interacting with the banana. This is a behavior reference; our Maker Arm controller still needs continuous runtime contact control to reproduce it.

### Aperture endpoint handling

The installed Dream client returns normalized aperture predictions unchanged beside its IK joint result (`dream/client_ik.py`, `IK.solve` output assembly); 0 means closed and 1 open. The simulation actuator saturates finite aperture demands to physical [0, 1] travel and records each requested/applied value under playback timing `grip_limit_corrections`. This models actuator endpoints, not a calibrated transmission. Nonfinite values and invalid joint positions still fail. Velocity retiming uses the applied gap and actual initial gap. Raw model responses remain unchanged in the debugger and are archived per cycle.

Live verification after the actuator adapter update: request `d34a7a828bcf40f3848ce3bba4132baa` retried the real microphone transcript “Pick up red box.” Its first 49-sample trajectory completed in 7.506 seconds, with maximum joint changes of 0.408 rad (left) and 2.363 rad (right). This particular reply needed no aperture corrections. Actual overhead frames show both arms changing pose, without picking up the cube. The 66-frame/13.2-second MP4 fully decoded; initial, middle and final frames were inspected. The app retained 21 three-camera packets and browser replay was verified. The second model call failed because the inference session expired/restarted server-side, so the overall command correctly reports failed. Continuous grasp behavior remains unverified.

## Camera panels: Live / Recording

The controls now sit immediately above the camera panels. Live shows refreshed Unreal JPEGs in all three panels, including while idle. Recording replaces those same images with native MP4 players (play/pause/seek) and per-camera downloads. New commands capture all three cameras at 5 fps; the worker encodes and uploads each bounded clip to the private Luna2 server. Earlier overhead-only captures explicitly show missing wrist recordings. The last clips and their command/result metadata survive a server restart; the transient frame replay API remains available separately.

Camera capture starts with the worker and remains active between commands, with a 24-hour local observer expiry. The server marks stalled image timestamps as STALE. The page identifies the last accepted prompt and distinguishes a failed command with completed motion from a command without completed motion. Run disables immediately during submission; playback.started is emitted only after Unreal accepts the trajectory.


`--action-rows 12 --cycles 16 --runtime-physics` runs a receding-horizon simulation trial: validate every returned row, execute only the first 12 predicted rows (0.6 seconds at nominal 20 Hz, slowed as necessary by joint speeds), then obtain fresh camera/state feedback before requesting again. Raw full responses remain archived. Prefix execution does not change the selected joint targets, skip joint-limit validation, or control the cube pose. It is intended to reduce open-loop contact overshoot; successful autonomous pickup remains unverified.

## Bounded Flash / full routing

The worker defaults to `--checkpoint auto`. Each command starts on Flash. After
eight completed control cycles, red-cube lift below 2 cm permits at most two
full-model inference calls (including session-renewal retries). Missing telemetry
keeps Flash. Full-model capacity refusals disable full for the rest of that
command and fall back to Flash with fresh camera/state feedback. Other failures
are surfaced; no rejected trajectory is replayed. Policy switches clear the old
observation window and wait for IK initialization before fresh feedback.

Activity events show each selected/called checkpoint and fallback, and archived
action files retain the checkpoint. An explicit `--checkpoint MODEL_ID` disables
automatic routing for controlled comparisons. The cycle limit still bounds
control iterations; this is a call-count policy, not a monetary spending cap or
a guarantee that the full model performs better. The 2 cm heuristic is not a
success criterion: the requested pickup still needs a visible, held 10 cm lift.

## Explicit local grasp-assistance experiment

`--grasp-assist --cycles 5 --tcp-back 0 --pad-contacts --runtime-physics` is an opt-in
hybrid test: one real Dream approach, then a local solver aligns above the
known red test block, descends, closes and settles, and commands an 11 cm
tool rise followed by a three-second hold. The extra 1 cm allows contact settling;
measured object rise must still reach 10 cm. Local phases use privileged simulated
prop position and are logged as `controller.assisted`, not Dream model output.
The solver returns only arm joints and jaw demands through the same bounded
controller. It never attaches the prop, writes prop transforms or disables its
gravity. This is not proof of an unassisted Dream policy grasp or hardware fidelity.
The fixture must be the declared 5×5×7 cm target at (60,-40,78.5) cm; arbitrary scene commands are
not supported by this experimental mode. Default mode remains unassisted.

`--pad-contacts` replaces only transient hidden jaw collision proxies with
3×1×2 cm flat boxes at flange-local (0, ±0.5, -17) cm, including each jaw's
prismatic motion. Their inner faces lie on the joint zero planes, matching
the sampled CAD surface at X=0/Z=-17 cm. This is a deliberately simplified
contact diagnostic; it is not a one-to-one collision mesh. Official visible arm
meshes remain unchanged. The contact limiter now accounts for the sampled
inner-face offset instead of equating joint travel with pad clearance.


### Position-priority IK diagnostic

`--position-ik --tcp-back 0` converts raw Dream end-effector positions using the
pinned source kinematics, while softly retaining the SDK-solved orientation and
preserving its jaw commands. It uses no prop position or scripted lift. Targets
more than 2 mm from the constrained solution are rejected; the ordinary joint
limits and speed retimer still apply. Raw and executed trajectories plus solver
errors are archived separately. This option excludes `--grasp-assist`.

`--verify-red-lift` holds the current robot pose when measured red-block rise
reaches 10 cm, and finishes after at least two seconds of a slow held rise. It
does not move the object. Visual review remains required to establish a grasp.
Flat-pad contact limiting uses the actual 3 by 2 cm pad footprint and zero inner
face offset, independently of the coarse CAD-jaw approximation.
