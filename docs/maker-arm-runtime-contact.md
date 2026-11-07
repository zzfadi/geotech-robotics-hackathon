# Runtime contact validation

The editor controller moves the official CAD links kinematically. It does not run rigid-body grasp physics or lift props. Completing a Dream trajectory therefore is not a successful pickup.

Two isolated Simulate-in-Editor probes distinguish basic physics from grasp contact:

- `maker_arm_runtime_probe.py` drops the orange 5 cm block 30 cm onto the table. The measured block falls and rests at 77.5 cm height. This verifies gravity and the tabletop collision only.
- `maker_arm_grip_probe.py` prepositions that block between the right jaws, closes from 8 cm to 4.5 cm over 1.5 seconds, enables gravity, raises the wrist through joint motion, then opens. It never attaches the cube or commands its lift path. This is a controlled fixture, not an autonomous pickup.

The latest fixture explicitly wakes the rigid body every tick. It collected 441 samples without an exception, ended simulation, and verified all original editor translations, rotations and scales were preserved. The cube did **not** remain between the jaws or rise with them; it fell to the table. Waking alone does not resolve contact. The compact result is in `docs/validation/maker-arm-jaw-contact.json`.

The fixture uses uncalibrated test parameters: cube mass 0.05 kg and friction 0.8. Its center is 4 cm behind the fingertip TCP. The current kinematic link collision uses the official URDF triangle meshes. Triangle contact, gripper force and runtime actuation still require investigation; this result must not be described as validated dynamics or a working grasp.

Run probes only while the worker is idle and after coordinating the shared editor task. They create a temporary simulation world and must never apply runtime changes back to the editor. The grip probe also rejects an existing simulation or shared animation.
