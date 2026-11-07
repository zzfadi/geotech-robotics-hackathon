# Maker Arm model and accuracy assessment

Researched November 7, 2026 after a new close-up clearly showed MakerMods / Maker Arm branding. This identifies the model family; the photograph does not establish the exact hardware revision or calibration. The earlier I2RT YAM preview is not the target arm.

## Official model found

Dream Labs provides Dream-1 (https://dreamlabs.ai/). MakerMods explicitly connects Maker Arm to Dream-1 and links its simulation model from https://www.makermods.ai/maker-arm.

- Official model: https://github.com/makermods-robotics/maker-arm-sdk/tree/main/urdf/maker_arm
- Entry point: `robot.urdf`; keep its adjacent `meshes/` directory.
- Pinned commit: `0245316c05cb3e8d900f35e70c08e2e18e36731e`.
- Unmodified local copy: `Unreal/TheHumanTouch/SourceAssets/MakerArm`, with upstream README, revision report, repository Apache-2.0 license and SHA-256 provenance manifest.
- Six revolute arm joints and two opposed prismatic jaw joints (one mimics the other), plus a fixed grasp frame. Model jaw opening is 0–104.825 mm.
- CAD-derived STL geometry, visual/collision definitions, mass/COM/inertia, origins, axes and limits are included. No native STEP/parametric CAD file was found in this SDK checkout. The product page describes further CAD/print-file releases in stages.

## Accuracy limits from upstream

This is the official model package, not a certified digital twin. The upstream README explicitly says physics/RL dynamics are unvalidated. Forearm/wrist inertials and joint friction are fitted, jaw mass is estimated, and gripper effort 20 N and velocity 0.1 m/s are placeholders. Arm zero alignment and actuator limits remain unverified. Gripper motor-to-gap transmission is not calibrated across its travel. Internal crank/rod geometry is omitted; finger loops are hidden visually but remain in collision/mass. Source part_003 is unresolved.

## Required before accurate simulation

1. Confirm the on-site arm revision and end effector against the manufacturer model; measure base poses and table coordinates.
2. Compare physical joint zero poses, signs, axes, limits and several measured tool poses with URDF forward kinematics.
3. Calibrate jaw gap against encoder angle at multiple points; verify linkage and grasp-frame location.
4. Obtain or identify measured link mass/COM/inertia, actuator torque/speed, damping, backlash and controller latency; compare simulated and recorded trajectories under known loads.
5. Build and validate convex collision geometry, jaw contact friction and self-collision exclusions in the chosen physics engine. A visual mesh import alone does not satisfy this.
6. Calibrate wrist/overhead camera intrinsics, extrinsics and timestamp alignment, then validate grasp/contact outcomes against the real station.

The CAD uses Y-up geometry; upstream mentions a +90-degree X inspection display rotation. Preserve the model frames and validate conversion to Unreal centimetres/handedness explicitly. Do not reuse YAM link offsets or joint limits.

## Current project state

The official source model is downloaded and its mesh references verified. Nine combined link meshes have been imported into the live Unreal scene, and both YAM arms have been replaced by 18 Maker Arm link actors. Imported bounds agree with the converted CAD vertices within 0.1 mm. All 18 actors were observed on Luna2 after Multi-User synchronization. This is currently an editor kinematic representation, not yet a physics articulation or calibrated physical twin. The map has been renamed to `/Game/Maps/TheHumanTouch`, verified active on Mac and Luna2, and selected for both default-map settings. The guarded binary checkpoint and confirmation of Brian’s active map remain unfinished.

## Physical orientation reference

The November 7 overhead photo places both arms on the same long table edge, reaching in parallel across the table. `maker_arm_station.py` now applies a 180-degree mount yaw to both official CAD assemblies and uses the folded zero pose; the gripper grasp frames point into +X and the elbows lie behind the bases. Dimensions and exact encoder pose are still unmeasured. Four offline checks cover model coordinates, symmetric jaws, invalid commands, and this mounting direction. The updated pose was observed on Luna2.

The photo-derived table arrangement uses a provisional 80 × 200 cm tabletop at 75 cm high, a shared mounting plate at 76 cm, the existing 80 cm arm spacing, a central camera mast, and a computer proxy at the far side. These dimensions are estimates pending measurement. `arrange_table()` changes furniture only and preserves the official arm geometry and scale; six approximate colored block/rod proxies occupy the shared work area in front of the grippers. The physical table clutter, camera intrinsics and laptop geometry are not modeled exactly.

## Physics preparation and collision checks

`Tools/maker_arm_physics.py` now derives nine rigid-body descriptions, eight movable joints and the fixed grasp frame from the hash-verified source. It converts the full rotated inertia tensors, COM, joint axes, limits and effort into Unreal units; the estimated source mass sum is 3.87155 kg including the base, not a measured hardware specification. Eight offline model/physics tests pass. Run `python3 Unreal/TheHumanTouch/Tools/maker_arm_physics.py` to regenerate ignored inputs under `Saved/CoPresenceImports/MakerArmPhysics`.

Nine separate collision meshes have been imported and shared as experimental assets. Whole-link Unreal convex decomposition failed the 0.5 mm vertex-coverage gate: initial coverage ranged from 89.49% to 96.94%, with maximum outside-plane margins up to 7.084 mm. Higher-resolution jaw decomposition did not resolve this. These assets are marked `SimulationReady=false`; the arm actors still use the kinematic visual representation.

`decompose_maker_arm_parts.py` evaluates CoACD 1.0.14 per original CAD solid in metre coordinates, preserving collision-only jaw geometry. The left-jaw trial at a 0.25 mm algorithm threshold produced 1,749 hulls; all 2,205 source vertices passed the separate 0.5 mm coverage check. A numerical seam in part_033 was welded on a 10 nm grid in derived data only. This trial is **not installed into Unreal**: hull count, cooking, excess collision volume and contact performance remain unvalidated. `apply_part_hulls` has a conservative caller hull budget (128 by default, not an engine limit).

Reproduce the offline trial with `uv run --script Unreal/TheHumanTouch/Tools/decompose_maker_arm_parts.py gripper_left`, then `uv run --script Unreal/TheHumanTouch/Tools/validate_maker_arm_collision.py --part-link gripper_left`. The validator without `--part-link` checks exported Unreal hulls. Vertex coverage cannot certify concavity fidelity or physical contacts. Native Chaos articulation and calibrated drives remain pending.

## Three virtual cameras

`Tools/maker_arm_cameras.py` creates or updates `MakerCamera_LeftWrist`, `MakerCamera_RightWrist` and `MakerCamera_Overview`. The wrist cameras attach to each arm's `link_007`, so they follow subsequent kinematic pose changes. The overview camera reuses the original camera actor and attaches to the central mast housing. These are actual Unreal CameraActors; select one in the Outliner and use Pilot to inspect its view. This does not yet provide synchronized dataset recording or ROS image topics.

All three render views were checked on the Mac; the same three cameras, their parents and FOV values were verified on Luna2 through Multi-User. The wrists show the jaw tips and tabletop; the central camera shows both arms and the shared task area. Provisional settings are 16:9, 90° horizontal FOV for wrists and 100° for overview. Extrinsics, sensor/lens intrinsics and distortion require real-camera calibration. The shared binary checkpoint remains subject to the persistence guard.

The subsequent front photo refines the central mount to a flat black mast, black top housing and underside lens. `refine_central_mount()` updates those visual proxies and repositions the overview optical center below the housing. The estimated mast is 1.5 × 7 × 66 cm; no camera make/model or measured lens calibration can be established from this photo.

Camera display meshes are now hidden at the user's request: all three CameraComponents have no editor camera mesh; the separate central housing/lens proxies are invisible in editor and game. The cameras remain active with their attachments and optics unchanged, confirmed on Luna2. `setup()` preserves this preference through `hide_camera_meshes()`.

A follow-up CoACD trial with `--merge` at the same 0.25 mm threshold reduced the left jaw from 1,749 to 1,686 hulls. All 2,205 source vertices still pass the 0.5 mm coverage check, but this small reduction does not resolve the unvalidated collision-performance and contact-fidelity requirements; the trial remains offline.
