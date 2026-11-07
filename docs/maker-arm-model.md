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
