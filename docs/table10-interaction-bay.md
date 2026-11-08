# Table10 interaction bay

Table10 is a separate human-interaction station outside the existing nine-table Dream grid. Its scene helper is `Unreal/TheHumanTouch/Tools/maker_arm_interaction_bay.py`. It makes no network or physical SDK calls.

The bay uses the original pinned MakerArm meshes, six-joint kinematics, symmetric grippers, table, camera mounts and accepted open-room materials. It contains no task cups. All40 cups and every pre-existing actor transform remain unchanged.

| Item | UE world coordinates (cm) |
|---|---|
| Table center | 2505,0,72 |
| Room floor center | 2505,0,−2 |
| Left arm base | 2480,−40,76 |
| Right arm base | 2480,40,76 |
| Overhead camera | 2472.5,0,138.5 |

The translation from original station coordinates is(2460,0,0)cm. Visible tabletop and wall/floor numerals are10. The68 bay actors use prefix `MakerInteraction_T09__`, folder `MakerInteraction/Table10`, and tags `MakerInteraction` / `MakerInteractionTable10`. They have **no MakerFleet tags** and are excluded from the nine-table controller. Both wrist cameras retain their link_007 attachment; the central camera remains attached to its mount. Two1800lm rectangular lights and one700lm point fill retain the accepted unshadowed styling.

Both arms start in the fixed clear home pose recorded by `HOME_Q` in the helper, with total jaw opening0.1m. Robot scale, CAD axes and source provenance are unchanged.

## Controller boundary

This deliverable is scene geometry and kinematic initialization. It does not implement force sensing, articulated compliant dynamics, or physical-arm control. Dragging a visual actor is not a force measurement. A later interaction controller must explicitly distinguish user intent, simulated contact/impulse measurements, and actual measured hardware feedback. It must address the interaction namespace rather than the Dream fleet, preserve CAD joint limits and calibrated coordinate mappings, and keep hardware execution separately controlled. No physical commands were sent while constructing or verifying this bay.

Scoped task`t-0234` created68 actors and modified/deleted no existing actors. Read-only Mac/Luna parity verifies the bay count, namespaces, bases, camera parents, number10 materials and lighting. See `docs/validation/table10-scene-parity-2026-11-07.json`.
