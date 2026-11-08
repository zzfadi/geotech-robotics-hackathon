# Controlled contact fixtures

Prepared 2026-11-07 after batch `20261107-164929-13d280`. This is a proposed test fixture, not a successful pickup result or calibrated hardware contact model.

`DreamSim/fleet_contact_fixtures.json` runs five existing props with the left arm and repeats four with the right arm (nine tables, including the original Table 1). Use configured mode with randomization disabled. All placement is before simulation. Props must remain independent, gravity-enabled Chaos bodies during motion; no attachment or transform-based lifting is allowed.

| Target | Physical dimensions (cm) | Center (table local cm) | Rotation (UE pitch,yaw,roll) |
|---|---|---|---|
| Red block | 5 × 5 × 12 | 60, ±40, 81 | 0,0,0 |
| Green cube | 5 × 5 × 5 | 60, ±40, 77.5 | 0,0,0 |
| Orange cube | 5 × 5 × 5 | 60, ±40, 77.5 | 0,0,0 |
| Orange block / yellow rod | 6cm cube, Ø2 × 14cm rod | 60, ±40, 78 | 0,90,0 |
| Orange cylinder | Ø5 × 16 | 60, ±40, 77.5 | 90,0,0 |

The cylinder diameter is deliberately enlarged from 3cm under the user's resize authorization. All other dimensions preserve the source scene observed during the audit. These dimensions are approximate scene props, not measured foam hardware. The red block is taller than the earlier 7cm experimental fixture.

The initial grippers are open 8cm around the target, with TCP centers independently checked using official URDF forward kinematics to within 0.00001cm of the listed centers. Both mounts use yaw pi. This isolates contact and lifting from long-distance approach planning; report that assisted starting condition with results. The inactive arm remains at its neutral pose.

Official total jaw opening is 10.4825cm. Runtime flat pads are an approximate 3×1×2cm pair; the finite contact corridor in flange coordinates is X±1.5cm, Z−18..−16cm. The target must cross the jaw center plane, not merely overlap a broad bounding box. The selected pose closes along world Y and approaches approximately along X. Rotating the rod assembly's long axis to world X presents the 6cm central block to the jaws instead of a 14cm total width. Likewise the cylinder lies along X. Cylinder rolling/slipping and compound-hull differences remain physics risks; the contact limiter uses clipped bounding boxes, not exact surface contacts.

The configuration requires full `target.rotation_deg` support in normalization and application. Yaw-only updates preserve previously simulated pitch/roll and are insufficient for repeatable fixtures. Targets are deliberately omitted from the separate `objects` list because that later placement loop otherwise overwrites target XY/height. Other props are parked at X30, Y−75,−15,15,75 to leave the tested grasp corridor clear.

## Why the preceding batch is not a contact verdict

Three tables stopped before motion because the policy orientation reference violated joint limits. Several others hit duration limits or unfinished cycles. Randomized layouts reset the grippers to a neutral pose and required approach planning; they did not reproduce the previously promising pregrasp fixture. The orange-cube raw-joints trial reached 4.17cm peak lift, while the earlier controlled red-block trial reached 9.38cm. Neither satisfied a 10cm lift held for two seconds. A missing or small lift therefore cannot be attributed solely to friction or graspability. Check action receipt, actual jaw contact, gravity-enabled retention, and the complete measured hold independently.

No live scene edits or paid inference calls were made while preparing this fixture file. Confirm full rotations and tabletop clearance in a scoped idle setup before launching it.

## Evidence helper

`maker_arm_grasp_evidence.update` is an isolated, tested accumulator awaiting runtime integration. It emits a **geometry review candidate**, not success, after two continuous seconds with: a free gravity-enabled body, at least 10cm center lift, speed at most 10cm/s, the lowest transformed collision-bound point at least 1cm above the table, both finite pad faces within 2mm of the object's collision surface, and at most 5mm target-to-TCP drift. Sampling gaps over 250ms reset the hold. Collision-bound bottom must be measured from transformed geometry, not substituted with center height. Distance sampling must cover the finite pad surface; a nearest point from an unrelated face or bounding-box overlap is insufficient.

A separate physical-contact candidate additionally requires a complete actual contact snapshot showing both jaws and no other contacting body. The current runtime does not supply that ledger; missing information therefore stays unverified. `contact_stop` labels must never stand in for actual contacts. The helper always leaves `grasp_verified=false`; independent synchronized video review plus physics provenance can establish the final review decision. These diagnostics must not delay the authorized trials while native contact telemetry is unavailable.
