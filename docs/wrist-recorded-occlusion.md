# Recorded wrist self-occlusion

Run `20261107-193913-f7d043`, tableT01 final observation, demonstrates a geometric limitation of fixed world-down rotation plus a rigid wrist-relative camera position. The approved home view remains correct. At the final right pose, the tool approach points upward (worldZ+0.330); the world-down camera looks back across its own wrist. The center ray hits official link007 part027 at2.906cm and part026 at5.810cm. Finalleft hits gripper_left part045 at1.814cm. The home center rays are clear for both arms.

An offline alternative preserves the exact approved home lens position,90degree FOV andworld-down orientation: position=official grasp_center worldposition + fixedworldoffset(1.935760,0.00000118,5.649637)cm. This is a translation-stabilized virtual sensor, not a physical rigid mount. It never aims at a cup or changes robot geometry.

Across358 recorded arm states from allnine tables, the alternative lens point was never belowtable or inside the checked own-arm mesh bounds. Large wrist/arm-body center-ray occlusions disappeared, but63/358(17.6%) center rays still hit gripper fingers;122/358(34.1%) had at leastone ofthefive inner rays blocked. In the problemT01finalright pose, the new center ray hits gripper_right at4.836cm. Therefore the alternative is not an all-pose visibility solution and was not applied. Some gripper appearance is expected in a wrist view; images would still need review before judging usability.

The audit uses exact supplied URDF visual geometry, both measured armstate streams, center/eightfrustum rays and tabletop distance cutoff. It is not an exhaustive continuous frustum intersection or collision proof. It checks each arm against its own meshes, not the otherarm or dynamicallymovingcups; archived per-command joint feedback does not reconstruct every intervening object pose. No live scene changes or inference occurred during this analysis. Root's subsequent instructed gripper-down pilot retains approved optics unchanged.

Evidence: [rigid T01](validation/wrist-recorded-occlusion/rigid-t01.json), [all-recorded alternative](validation/wrist-recorded-occlusion/tcp-stabilized-all-recorded.json).

Provenance: official Maker Arm SDK/URDF commit0245316c05cb3e8d900f35e70c08e2e18e36731e; approved virtualcamera source commit e34a9e8. The358samples are recorded command-start observations plus finaltable observations, not every interpolated render/physics frame. Geometry calculations were offline using trimesh and the project mountedFK; no motor or API connections.
