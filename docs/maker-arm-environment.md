# Maker Arm room environment

## Current grid lighting

The live grid uses `maker_arm_grid_scene.py`: eleven stations in three columns at 820 cm spacing, 8 m walls and open tops. Each station keeps its original table-local equipment and cameras. Flat tabletop labels show 1–11.

A shared 1800 lux unshadowed directional daylight source, ambient cubemap intensity 2.5 and two unshadowed 1800 lumen ceiling RectLights per room replace the earlier dense light rig. Screen-space global illumination is selected (method 2), not Lumen; mesh distance fields are not enabled. Fixed exposure uses −0.5 EV compensation to retain pale-table and bright-prop highlights.

Each room also has one movable, unshadowed 700 lumen / 5000 K point fill, centered 230 cm above the floor. Its 500 cm attenuation radius limits overlap. The light stops drawing beyond 1800 cm with a 300 cm fade, avoiding eleven additional shadow maps. Reapplying global illumination preserves this point-fill culling. All 33 local lights are unshadowed; mild screen-space ambient occlusion (intensity 0.5, radius 50 cm) supplies grounding without direct-light shadow maps. These lights approximate room bounce; they are not a calibrated physical-lighting reconstruction.

Before the later shadow-free lighting change, an eight-second idle Mac sample with the point fills enabled measured 45.35 FPS (median 21.58 ms, p90 25.29 ms, 0.39 frames over 80 ms per second). The earlier sample was 36.04 FPS, but the camera moved between samples, so these are observations rather than a controlled estimate of point-light cost.

## Earlier single-room setup

`Unreal/TheHumanTouch/Tools/maker_arm_environment.py` adds an empty, reference-inspired industrial hall around the existing station. It uses the user's November 7 hall photo: pale walls, gray-beige floor, cool side daylight and neutral overhead strips. The foreground table remains the existing station; no people, extra tables, chairs or signage are added.

The helper is idempotent and must run within a scoped CoPresence task. It owns `MakerEnvironment_*` actors and raises `MakerLighting_Ambient` intensity to 1.1. It compares numeric station, robot, task-object and camera transforms before and after; arm geometry, poses, tabletop equipment, task-object colors and virtual-camera transforms are preserved.

The approximate room is 8 × 8 × 4 m. Its cube floor is 4 cm thick with its top at Z=0, matching the existing table-leg bottoms, and uses BlockAll query/physics collision. Walls, beams and strip housings are visual-only and outside the robot workspace. The two 200 cm strip housings run along world Y, matching the RectLights' 200 cm source width. Broad 6500 K side fill and 4500 K ceiling lights supplement the existing station lights; exposure remains fixed.

These dimensions, surface colors and light powers are artistic estimates, not measured room geometry or calibrated photometry. Material appearance is a plain matte approximation, not a scan of the floor. Physics contact against the floor uses the built-in cube's simple collision; no real-world floor-contact validation is claimed.

## Live verification

On November 7, tasks t-0035 through t-0037 created 13 room/light actors and shared three new material assets. Protected station transforms compared equal. The floor top is 75 cm below the tabletop top and touches the table-leg bottoms without intersecting the tabletop. The existing actor inventory contained no human performers, and none were added. Editor images show a grounded table with softly lit arms and pale surroundings rather than the previous black void.

Three-camera and remote replica verification are recorded by the overall integration audit after the runtime observer resumes. The source helper does not save or checkpoint the level itself; Luna2's guarded checkpoint owns persistence.

Fresh overhead and both wrist frames were visually checked after the observer resumed (sequence 8, age 0.1 seconds). All three showed the new surroundings, readable arm surfaces and preserved task colors, with no new occlusions. The optional `add_window_context()` is prepared for a subsequent coordinated task: three shallow daylight panels with pale frames along the left wall. It is not part of `setup()` and was applied separately in task t-0040 (21 additional actors; 34 environment actors total). Numeric station transforms compared equal. Window panels are shallow artistic representations, not transparent openings or measured window geometry.

Multi-user read-only verification subsequently confirmed Mac and Luna2 both have 34 environment actors, identical five-material sets (including actual window-frame assignments), Floor `QUERY_AND_PHYSICS`/`BlockAll`, and ambient intensity 1.100000023841858. Environment scenery and these component settings replicated successfully to both connected editors.

## Painted navigation numbers

`maker_arm_wall_numbers.py` imports eleven transparent numeral textures as deferred-decal materials. T00–T10 map to 1–11 on two opposing walls and one floor projection per room; original tabletop stickers remain. The floor location is table-local (220, 200, 2) cm, outside the tabletop footprint. Billboard and Arrow visualization components are hidden. Source textures live under `SourceAssets/Environment/WallNumbers`.

Visual QA found and repaired incomplete material settings left by a rolled-back import. All eleven materials were rebuilt and shared. A fixed square wall capture then exposed reversed numeral orientation, and the first floor capture did not show its number. Further scenery iteration was stopped at the user's request in favor of fleet testing. Do not claim final wall orientation or floor-number visibility verified. Room 7 retains a diagnostic projection override (back-wall yaw 0 / roll 90; floor pitch −90 / roll 0 / depth 32 cm); other rooms use the helper defaults. These remaining presentation issues do not change robot geometry, cameras, physics or task targets.

Final read-only Mac/Luna2 audit matched all eleven room floor offsets, zero roofs, 8 m walls, 1800 lux unshadowed sun, ambient 2.5, exposure −0.5 EV, 34 unshadowed direct lights (including eleven 700-lumen point fills culled at 1800 cm), 33 translucent decal-material assignments, eleven loaded numeral textures and numeric tabletop texts 1–11. All 44 number actors have editor billboard scale zero, with no visible decal Billboard/Arrow components. See `validation/environment-parity-2026-11-07.json`. This verifies replicated scene properties, not the unresolved decal presentation noted above.
