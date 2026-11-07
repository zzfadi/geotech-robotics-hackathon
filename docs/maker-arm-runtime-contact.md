# Runtime contact validation

The editor controller moves the official CAD links kinematically. It does not run rigid-body grasp physics or lift props. Completing a Dream trajectory therefore is not a successful pickup.

Two isolated Simulate-in-Editor probes distinguish basic physics from grasp contact:

- `maker_arm_runtime_probe.py` drops the orange 5 cm block 30 cm onto the table. The measured block falls and rests at 77.5 cm height. This verifies gravity and the tabletop collision only.
- `maker_arm_grip_probe.py` prepositions that block between the right jaws, closes from 8 cm to 4.5 cm over 1.5 seconds, enables gravity, raises the wrist through joint motion, then opens. It never attaches the cube or commands its lift path. This is a controlled fixture, not an autonomous pickup.

The latest fixture explicitly wakes the rigid body every tick. It collected 441 samples without an exception, ended simulation, and verified all original editor translations, rotations and scales were preserved. The cube did **not** remain between the jaws or rise with them; it fell to the table. Waking alone does not resolve contact. The compact result is in `docs/validation/maker-arm-jaw-contact.json`.

The fixture uses uncalibrated test parameters: cube mass 0.05 kg and friction 0.8. Its center is 4 cm behind the fingertip TCP. The current kinematic link collision uses the official URDF triangle meshes. Triangle contact, gripper force and runtime actuation still require investigation; this result must not be described as validated dynamics or a working grasp.

Run probes only while the worker is idle and after coordinating the shared editor task. They create a temporary simulation world and must never apply runtime changes back to the editor. The grip probe also rejects an existing simulation or shared animation.

## Collision A/B diagnostic

The identical fixture passed when **only the two runtime jaw components** used the existing simple convex collision meshes (left 40 hulls, right 50). It retained the cube under gravity, lifted it more than 5 cm by jaw contact, and released it back onto the table. There was no cube attachment or commanded cube trajectory. The original editor transforms were preserved and SIE ended cleanly. See `docs/validation/maker-arm-jaw-convex-diagnostic.json`.

This isolates the moving triangle collision path as a substantial cause of the failed fixture. These existing coarse hulls have not passed geometric/contact fidelity validation and are diagnostic, not a one-to-one collision replacement. The high-resolution offline left-jaw decomposition requires 1,686 hulls and was not installed; its sampled geometric checks must not be attributed to the live 40-hull asset. Autonomous Dream pickup is still unproven.

The camera observer now pauses during SIE and performs up to three local actor-rebind attempts afterward, because SIE invalidates Python camera wrappers. This A/B run also verified automatic resumption of all three camera streams with a 0.22-second frame age after SIE ended.

## Contact-limited closing diagnostic

A second A/B test commanded a 1 cm opening around the 5 cm fixture cube. With ordinary kinematic jaws the cube was expelled and fell; retention and lift failed. With an explicit contact-stop approximation, the same command retained, lifted and released the cube. Both tests ended SIE and preserved editor transforms. Compact evidence is in `maker-full-close-contact.json` and `maker-contact-stop-test2.json` under `docs/validation`.

`maker_arm_contact_stop.py` reads prop mesh bounds in the official flange frame and limits inward travel when an object spans both inner jaws in the pad corridor. It permits 5 mm total compliance, leaves openings unrestricted, and reports the actual applied gap back to Dream. It never attaches or writes a prop pose; Chaos gravity and friction still determine lift and slip. This is a geometric position-actuator approximation, **not** calibrated force control or a one-to-one physical gripper. Seven offline checks cover empty closure, centered contact, release, out-of-corridor objects, different sizes, and no forced reopening. Autonomous Dream pickup remains a separate acceptance test.
