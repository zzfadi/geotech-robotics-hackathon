# Maker Arm room environment

`Unreal/TheHumanTouch/Tools/maker_arm_environment.py` adds an empty, reference-inspired industrial hall around the existing station. It uses the user's November 7 hall photo: pale walls, gray-beige floor, cool side daylight and neutral overhead strips. The foreground table remains the existing station; no people, extra tables, chairs or signage are added.

The helper is idempotent and must run within a scoped CoPresence task. It owns `MakerEnvironment_*` actors and raises `MakerLighting_Ambient` intensity to 1.1. It compares numeric station, robot, task-object and camera transforms before and after; arm geometry, poses, tabletop equipment, task-object colors and virtual-camera transforms are preserved.

The approximate room is 8 × 8 × 4 m. Its cube floor is 4 cm thick with its top at Z=0, matching the existing table-leg bottoms, and uses BlockAll query/physics collision. Walls, beams and strip housings are visual-only and outside the robot workspace. The two 200 cm strip housings run along world Y, matching the RectLights' 200 cm source width. Broad 6500 K side fill and 4500 K ceiling lights supplement the existing station lights; exposure remains fixed.

These dimensions, surface colors and light powers are artistic estimates, not measured room geometry or calibrated photometry. Material appearance is a plain matte approximation, not a scan of the floor. Physics contact against the floor uses the built-in cube's simple collision; no real-world floor-contact validation is claimed.

## Live verification

On November 7, tasks t-0035 through t-0037 created 13 room/light actors and shared three new material assets. Protected station transforms compared equal. The floor top is 75 cm below the tabletop top and touches the table-leg bottoms without intersecting the tabletop. The existing actor inventory contained no human performers, and none were added. Editor images show a grounded table with softly lit arms and pale surroundings rather than the previous black void.

Three-camera and remote replica verification are recorded by the overall integration audit after the runtime observer resumes. The source helper does not save or checkpoint the level itself; Luna2's guarded checkpoint owns persistence.

Fresh overhead and both wrist frames were visually checked after the observer resumed (sequence 8, age 0.1 seconds). All three showed the new surroundings, readable arm surfaces and preserved task colors, with no new occlusions. The optional `add_window_context()` is prepared for a subsequent coordinated task: three shallow daylight panels with pale frames along the left wall. It is not part of `setup()` and was applied separately in task t-0040 (21 additional actors; 34 environment actors total). Numeric station transforms compared equal. Window panels are shallow artistic representations, not transparent openings or measured window geometry.

Multi-user read-only verification subsequently confirmed Mac and Luna2 both have 34 environment actors, identical five-material sets (including actual window-frame assignments), Floor `QUERY_AND_PHYSICS`/`BlockAll`, and ambient intensity 1.100000023841858. Environment scenery and these component settings replicated successfully to both connected editors.
