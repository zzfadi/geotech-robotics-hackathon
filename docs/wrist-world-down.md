# Wrist cameras perpendicular to the tabletop

Both simulation wrist CameraActors now keep absolute world rotation while retaining wrist-relative position. Their actual CameraComponent optical forward is world (0,0,-1), image up is +X, and image right is +Y. Both sides use the same right-handed orientation, with no mirrored image or role swap. The overview and physical Table10 are unchanged.

Fleet policy exports, the single-station observer and expert recorder explicitly use the same rotation helper. This prevents a stale actor rotation from silently changing actual Dream input. The current user-approved virtual sensor position is flange-local(3,0,-11.8)cm with90-degree HFOV; both remain uncalibrated. See [source audit and current evidence](maker-arm-camera-source-audit.md).

Live task t-0294 checked all18 simulation cameras: dot(optical forward, world down)=1.0. T00 both arms were moved to a bounded alternate joint pose; their camera positions moved but both optical dots stayed1.0. All arm transforms were then restored, with0cm maximum positional error. No inference or physical simulation was run. The saved audits also include unchanged physicalTable10 cameras for comparison; those are deliberately outside this change.

Images: [left idle](validation/wrist-world-down/idle-Left.jpg), [right idle](validation/wrist-world-down/idle-Right.jpg), [left moved](validation/wrist-world-down/moved-Left.jpg), [right moved](validation/wrist-world-down/moved-Right.jpg). Numeric CameraComponent measurements: [idle](validation/wrist-world-down/idle.json), [moved](validation/wrist-world-down/moved.json), [restored](validation/wrist-world-down/restored.json).

Two offline tests cover identical left/right/replica optical bases and preservation of overview/physicalTable10. Source compilation passes. Runtime sources were copied to the live project; the runtime module must reload before the next policy session. No new Dream API image packet was sent during QA.
