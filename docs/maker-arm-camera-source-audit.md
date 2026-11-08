# Maker Arm camera-source audit — 2026-11-07

No measured camera-to-wrist transform or lens calibration was found in the official sources reviewed. The current simulation offset (14,20,8) cm and110-degree HFOV are experimental; correcting world rotation did not make those positions physical.

## Official sources

- [MakerMods Maker Arm](https://www.makermods.ai/maker-arm) links the SDK and URDF, and identifies included wrist/overhead USB cameras. It does not publish lens FOV, intrinsics, distortion or hand-eye calibration on that page.
- [Official SDK and URDF at0245316](https://github.com/makermods-robotics/maker-arm-sdk/tree/0245316c05cb3e8d900f35e70c08e2e18e36731e/urdf/maker_arm): remote HEAD still matches the imported revision. The URDF contains no camera/sensor/optical link or joint. Geometry uses numbered STL parts; it does not identify a lens optical center or camera mount datum. The omitted CAD part003 has an explicitly unresolved identity. Do not infer it is the camera.
- [Official MakerModsLab at e377149](https://github.com/makermods-robotics/makermodslab/tree/e377149419eebef7fa0f694b0d616c9c2cd46231): camera role/binding, capture and streaming infrastructure found; no Maker-specific lens model, FOV or calibrated extrinsic constants found in the searched Python/docs sources. Viewer perspective FOV is not a robot sensor specification.

## This physical bench

[Physical vision record](../arm/docs/control-and-vision.md) reports identical Sonix/KD-USB UVC devices, captured640x480 frames and advertised formats through1920x1080. These are capture formats, not lens calibration. Wrist A/B were not yet registered to physical left/right serials; intrinsics, distortion and extrinsics remain unknown. [Calibration evidence](../arm/evidence/2026-11-07-camera-arm-calibration.json) explicitly sets camera_arm_alignment_complete=false. Referenced local images were absent from both checked project roots; no hardware was queried.

## Secondary simulation reference, not manufacturer calibration

[youtalk/maker_arm_ros2 model.py](https://github.com/youtalk/maker_arm_ros2/blob/17ca897293337767ec26835c8a2b3c7e30bf50a0/maker_arm_mujoco/maker_arm_mujoco/model.py#L49) places a simulated camera at link007-local(0.030,0,-0.118)m:30mm from tool axis and5mm in front of motor face, pitched30degrees toward grasp_center, with70degree FOV and square224pixel images. Its comments explicitly label this mount WRIST_CAM_UNVERIFIED and say no real camera has fixed either roll orientation. Those numbers must not be promoted to exact MakerMods hardware specifications.

A geometry-grounded **candidate** for a future bounded visibility test is therefore local(3,0,-11.8)cm instead of the25.7cm experimental stand-off. Retain the user's world-down optical direction if evaluating that candidate; the secondary source's30degree pitch is a different requirement. Its square-image70degree FOV cannot be copied as a measured horizontal FOV for our320x176 frames. The candidate was subsequently compared in temporary scoped task t-0296, then fully restored; no global candidate was retained.

With a perspective camera pointing straight down, objects away from its optical axis can still show their sides. That effect does not by itself establish a tilted optical axis. The previous audit measured actual CameraComponent world-forward dot down=1.0; remaining viewport differences concern placement, perspective/FOV, occlusion and sensor calibration. Overview is unchanged.

## Bounded compact-lens comparison

Task t-0296 rendered both wrists at idle and alternate near-grasp poses, preserving world-down axes and comparing70/90degree horizontal FOV. At idle, the gripper fingers bracket an empty tabletop region; the distant blue cup is outside the narrow wrist view. At the moved left pose, the blue cup is centered and fills most of the view;90degrees retains more rim/finger context than70degrees. Both right views show the matching jaw-centered view. No robot geometry was hidden. These are static rendered poses, not a pickup.

All arm transforms and previous virtual camera transforms/FOV were restored before task end; maximum arm positional restoration error0cm. Overview remained untouched. Images are under [compact-wrist-camera](validation/compact-wrist-camera/). Official STL surface-distance checks found18.3038mm nearest mesh clearance at both poses; watertight meshes contain no lens point. All remaining open-mesh AABBs exclude the lens point at both poses. This checks a point, not the physical camera housing.

The compact90 near-left image still clips the8cm cup rim: its lens is only4.94cm above the rim, so the320x176 image has about5.44cm vertical coverage there. Root rejected this as a usable policy view. A subsequent single candidate at(11,0,-6)cm/90HFOV was also rejected: nearest source-mesh distance5.398mm and no point containment, but the downward view was obscured by nearby wrist geometry at the alternate pose. A clear lens point does not guarantee a clear view ray. Task t-0297 restored all camera and arm transforms; neither candidate was applied globally.

## User-approved current configuration

The user's later approval of the symmetric-jaw90degree reference superseded the earlier close-up rejection. Task t-0299 applied compact flange-local(3,0,-11.8)cm with90degree HFOV and absolute world-down optics to all18 simulation wrist cameras. Overview, physicalTable10 and arm poses were unchanged. This is the approved **virtual** configuration, not manufacturer-calibrated optics. Near-left cup-rim clipping remains a known limitation; it was not hidden or represented as solved. Current images and numeric audit are `approved-current-Left.jpg`, `approved-current-Right.jpg` and `approved-current.json` in the evidence directory. No inference was made during camera QA.

Three further upper positions were rejected offline without rendering: their world-down center rays struck official arm geometry within0.13–1.58cm. No alternative was applied after user approval.
