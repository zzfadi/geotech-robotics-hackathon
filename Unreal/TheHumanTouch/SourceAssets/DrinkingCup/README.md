# HoldMyBeer drinking cup

Procedurally generated project-owned OBJ by `Tools/maker_arm_cups.py`, 2026-11-07. This is an assumed presentation/test cup, not manufacturer CAD.

- Upright Z, centered origin; 12cm tall, outer rim diameter8cm, outer base diameter6cm.
- Wall thickness0.2cm radial; base thickness0.3cm. Open top with modeled inner wall and bottom.
- 256 vertices,194 polygon faces; every undirected edge belongs to two faces. Signed shell volume61.514cm³.
- UE asset `/Game/_Agents/Denys_Mac_Codex/MakerArm/Cups/SM_DrinkingCup` recomputes normals/tangents. Ivory opaque default-lit material, no emissive visibility override.
- UE convex decomposition produced20 hulls. It is approximate: central downward simple-collision trace hits Z75.061 at a cup centeredZ81, while the visual bottom is75.30. A near-wall ray at centerX+3.5cm hits simpleZ84.54 versus visualZ83.49. Thus the cavity is open, but collision is not exact thin-wall geometry.
- Runtime assumptions: mass0.05kg, friction0.8, no liquid; these are uncalibrated simulation parameters.
- Canonical actors `MakerStation_Prop_Cup`, `MakerStation_Prop_Cup_02`, `_03`, `_04`, and optional `_05`; replicas use `MakerFleet_T01__`…`T08__` prefixes. Tables contain `4 + index % 2` cups (40 total), with seed20261107 positions recorded in `DreamSim/cup_scene_layout.json`. Each independent RNG is seeded by string`seed:index`; X48–66cm, Yslots−60,+60,−30,+30,0 with±3cm jitter, centerZ81cm, identityrotation. Centers are separated by more than25cm.
- Removed both unrelated tabletop boxes (`MakerStation_UnverifiedControlProxy`, `Dreamlabs_YAM_ControlComputerProxy`) on every table. Robot mounts, camera hardware, numeric stickers and all room geometry remain.
