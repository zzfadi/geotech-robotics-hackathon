# Dream → Maker Arm Unreal integration

Verified November 7, 2026: `dreamlabs/dream-1-beta-flash` accepted the three Unreal camera images and measured CAD joint state for “Move the left gripper toward the red cube on the table.” It returned two 48 × 7 trajectories in 2.73 seconds. Both were played through all 48 rows in the live shared Unreal scene in one editor transaction, then the starting pose was restored. This proves text-to-kinematic-motion integration, **not** a successful grasp, continuous feedback controller, physics articulation, or voice control. Those remain in progress.

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
