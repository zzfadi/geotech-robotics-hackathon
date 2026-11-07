# Provisional wrist-camera calibration trial

This is a controlled scene/observation experiment, not a measured hardware calibration. The first six-cycle physics command missed the cube; its raw EEF targets already contained most of the task-position error.

The original wrist lenses were flange-local (12, 0, -12) cm, aimed at (-10, 0, -35) cm. Trial positions are (4, 0, -4) cm, aimed at (0, 0, -30) cm, with the same 90-degree horizontal FOV and 16:9 aspect ratio. This reduces the provisional lateral offset and aligns the optical axis more closely with the physical reference's gripper-mounted view. Both wrist cameras remain attached to link_007; the overhead camera is unchanged.

Before inference, both arms were explicitly moved to canonical REST with 6 cm jaw gaps. The single red cube was placed at (55, -30, 77.5) cm in the left arm's reachable workspace. The unverified red control-button proxy was assigned the existing neutral dark material to remove a second red distractor. Other table props remain. These are declared setup changes; the controller must never write the cube's path during a Dream command.

An immutable observation packet and its exact three JPEGs were captured at Unix 1794091508.2354538. Their hashes, initial scene snapshot and trial settings are in `docs/validation/maker-arm-provisional-camera-trial.json`. Full and Flash inference should receive this identical packet before comparing their raw EEF target paths.

The task acceptance criterion is an actual physical grasp, at least a 10 cm rise above the initial center height (77.5 to at least 87.5 cm), and a stable hold, visible in recordings and replicated views. Merely closing a gripper, moving an arm, bumping/sliding a cube or completing API cycles is not acceptance.

## Same-input model comparison

Both models received the same frozen frames/state and the exact prompt: “Use the left arm to pick up the red cube, lift it straight up by 10 cm, and hold it.” Flash's raw left EEF path approached the target center within 0.385 cm, and its solved CAD TCP path within 0.257 cm (final TCP distance 2.885 cm). Full's solved minimum/final distance was 7.476 cm. Final Flash IK positional error versus its raw target was 0.037 cm. No probe action was applied to Unreal.

This supports testing Flash on the prepared scene. It does not isolate the camera change from the simultaneously changed target location, visual distractor and more specific prompt, nor prove a physical grasp. Flash's final predicted jaw gap was 6.47 cm, still wider than the 5 cm block.

Runtime telemetry now records measured prop XYZ, velocity, height above initial, maximum consecutive time >=10 cm above initial, and maximum consecutive time at that height with speed<=10 cm/s. A one-second slow-height threshold is reported separately. `grasp_verified` remains false: a surface or collision ejection can satisfy numerical height criteria, so visual/contact review is still required. Five offline tests cover continuous intervals, fast/ejected motion, reset below threshold and invalid measurements.


## Close-range correction (15:00)

The initial 4 cm offset put the optical center inside the actual wrist shell, and the default capture near plane clipped close cube/jaw surfaces. Bounds read from the imported CAD wrist were X -3.10..10.79 cm and Z -11.28..-2.58 cm. Wrist optics now sit at flange-local (5,0,-12) cm, outside the shell and jaw X extent, looking toward (0,0,-17) cm. All local captures explicitly override near clipping to 1 cm. A rendered wrist image was inspected and shows solid jaw surfaces and the red target. These remain provisional virtual optics, not measured physical-camera calibration.

The user authorized larger/longer target geometry. The red test object is now 5 x 5 x 8 cm with center (55,-30,79) cm, resting on the 75 cm tabletop. Acceptance remains a contact-held lift of at least 10 cm (center >=89 cm), sustained and recorded. This setup change itself is not a pickup.

The taller 5x5x8 cm target tipped during the next recorded trial without a successful lift. The subsequent controlled setup uses a stable 7x7x7 cm cube at (55,-30,78.5) cm and a 9 cm initial jaw gap. The acceptance center height is 88.5 cm. The observer and the policy now use the same corrected close-range renders.
