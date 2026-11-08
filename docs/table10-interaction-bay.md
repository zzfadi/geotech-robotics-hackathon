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

## Measured-pose receiver

`Tools/maker_arm_measured_pose.py` adds a runtime-only `RuntimeReceiver`. It is deliberately inactive by default and has no transport, callbacks, physical commands or editor-world fallback. The owner must start an appropriate SIE/game-world session and invoke it on Unreal's game thread. Construct it with that exact game world and a registration mapping such as `{'left': {'serial': expected_serial, 'calibration_id': expected_calibration_id}}` taken from the validated calibration registry. Do not accept registration identities from an arbitrary incoming packet.

Call `apply(packet)` for packets produced by `Interaction.feedback_pose.measured_feedback_pose`. It requires Table10, the pinned arm identity, `pose_source='measured_sdk_feedback'`, `physical_output_enabled=False`, finite official URDF radians, and feedback no older than250ms or in the future. Accepted timestamps must increase. Updates are limited to10Hz per registered arm; the existing pose setter skips unchanged link transforms. Base position/orientation and unit scales are checked before applying a sample. Moving/replacing the bay requires explicit re-registration rather than silently shifting the model.

The receiver resolves only `MakerInteraction_T09__MakerArm_<side>_<link>`. Its private alias mapping lets the existing `maker_arm_fleet.set_pose(9,...)` reuse the verified coordinate conversion without renaming actors or changing global prefixes. It cannot resolve the original or other fleet arms. Wrist cameras follow their existing attachment.

An unknown `jaw_gap_m=None` **does not close the gripper**. The receiver checks that both current virtual jaw origins lie symmetrically on the official wrist prismatic axis with matching orientations and unit scale, then preserves that virtual gap while moving with the measured wrist. It reports `jaw_source='preserved_virtual_geometry'` and `jaw_measured=False`. This is visual continuity, not inferred physical gripper calibration. If the existing jaw geometry is inconsistent, the sample is rejected before any setter. A valid measured gap uses the total opening in metres, bounded by0..0.104825.

`close()` disables the receiver. Missing/stale feedback is not extrapolated; the transport/UI must show freshness rather than presenting a held visual pose as live hardware. SIE teardown naturally discards its visual changes. This helper creates no editor transactions and does **not** itself replicate runtime poses to other editors; that is a separate bounded mirror/transport concern. No live receiver or synthetic measured-feedback animation was started during implementation.

Validation: seven isolated tests cover freshness/identity/order/limits, rate limiting, exact namespace isolation, unknown-gap preservation, malformed jaw geometry, editor-world rejection, and no setter calls for invalid/rate-limited data. Run `python3 -m unittest discover -s Unreal/TheHumanTouch/Tools -p test_maker_arm_measured_pose.py`.
