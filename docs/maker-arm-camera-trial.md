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

## Reachable target and continuous motion

The current trial places the 7 cm cube at (45,-40,78.5) cm. Offline constrained
IK found feasible grasp and 10 cm raised tool poses at this nearer position; the
previous (55,-30) position had residual errors for the tested orientation. This
is a setup change, not a commanded cube trajectory. Policy targets now interpolate
continuously between their speed-limited samples at each editor tick.

Trial `2faa90163f5548b297fe878be8175654` completed 16 feedback cycles with eight
Flash calls, two full calls and six further Flash calls. Three 313-frame camera
recordings were uploaded. Maximum cube rise was only 0.4116 cm: no pickup.
The wrist recording and transformed box bounds exposed excessive width in the
contact-stop estimate for an oblique cube. The revised estimate clips the box
to the finite finger-pad corridor before measuring closure width; physical
retention remains Chaos-driven and is under live test.

The photo-inspired orange 6 cm block with a yellow 2 cm diameter, 14 cm through-rod
is one rigid compound mesh. A separate orange cylinder is 3 cm diameter × 16 cm.
Dimensions, mass and contact properties are approximate. The fresh compound
asset has three convex hulls and both added props entered Chaos simulation in
the completed trial. An earlier invalid import was replaced after a collision
rebuild crash; guarded host persistence of the fresh asset remains pending.

## Contact-depth trial

Finite-pad clipping alone reached only 1.2921 cm maximum rise in trial
`3949e37485a844dc84ae5343b5059f34`; it did not produce a held pickup.
The controlled contact fixture had retained a block 4 cm behind the official
fingertip-oriented TCP. An opt-in `--tcp-back .04` now lets Dream control that
virtual contact center without changing the source URDF, visual mesh, joints,
or fixed grasp_center frame. Default offset remains zero. No hardware
calibration is claimed or recorded.

For this experiment the red target is 7×7×14 cm, centered at (45,-40,82) cm,
resting on the same 75 cm tabletop. Its extra height permits deeper finger
engagement above the table. A successful lift requires center Z≥92 cm and
visible physical retention. The target is never attached or moved by the
controller. Command `2ec0a869dada4736b51ed955d4068508` tests this setup.

The deeper top-down trial still failed (maximum rise 0.726 cm). A first assisted
top-down trial tipped the tall block, then correctly stopped when it left the
fixture bounds. The next local-controller experiment uses a side-on orientation
(-80 degrees about task-frame Y) and a 5×5×7 cm block at (60,-40,78.5) cm.
Offline IK verifies both the initial and lifted poses while allowing the finger
tips to remain above the table. These are explicitly assisted trials, not claims
of unmodified Dream-policy success.

## Verified assisted lift and remaining policy test

The wrist-body interference was removed by restoring the source TCP and placing
the optional flat collision pads at flange-local (0, ±0.5, -17) cm. The deeper
-13 cm pad experiment is not the successful configuration. Each transient pad
is 3×1×2 cm; this contact approximation preserves official visual geometry.

Assisted run `45fbabd2060540718a47a716b743303d` physically lifted the free block
10.2679 cm and held it slowly above 10 cm for 4.2367 seconds. Repeat
`b13e9ce98c544846b94c8a91f8f95ae3` reached a final 10.8400 cm rise. The repeated
run recorded gravity enabled, active physics and no parent attachment. All three
147-frame MP4s decoded successfully at 5 fps. Visible frames were inspected.
Both runs made one actual Dream request, then used explicit local grasp
assistance; they are not evidence of an unassisted policy pickup.

The repeat's Mac/Luna2 audit compared 571 and 645 actual pose samples: 72
intermediate left-wrist poses matched, all 26 final actor transforms agreed
within numerical roundoff, and both editors observed the red block at Z=89.34 cm
from an initial 78.5 cm. Brian's editor remains unavailable for direct verification.

A subsequent unassisted test starts with the left gripper open around the block
on the table. Its initial joints are [0,-2.1318205591,0.4402033511,1.6916171860,0,0]
and 8 cm jaw travel; the right arm remains at REST. This is an explicit initial
pose change. All motion after submission is from Dream responses, with local
grasp assistance disabled. The contact model remains the simplified flat pads.
