# Free Wave can (Table 10 handover object)

Denys's model of a 12 oz Athletic Free Wave can. On Table 10 it is one actor, `MakerInteraction_T09__HandoverCan`,
showing the whole can as one mesh (`/Game/_Agents/Denys_Mac_Codex/MakerArm/Cans/FreeWave/SM_FreeWaveCan/StaticMeshes/SM_FreeWaveCan`),
so it moves as one piece in every editor. The pivot is at the can's centre (rest height 81.1 cm, radius 3.3 cm,
12.2 cm tall), which is where the Table 10 Dream runtime expects it. Collision is complex-as-simple so the VR
laser (a Visibility trace) can grab it.

- `free_wave_can.glb`, `free_wave_can.blend`, `build_can.py`, `label.png`: the model as built in Blender
  (seven parts), its source and the label texture.
- `free_wave_can_whole.glb`: the same can as one mesh, made by
  `python3 combine_can_glb.py free_wave_can.glb free_wave_can_whole.glb`. That bakes each part's transform into
  its vertices, moves the origin to the can's centre and puts all parts in one mesh with their three materials.
  Import it as `SM_FreeWaveCan.glb` (a one-mesh glb takes the file's name), as `Tools/QuestVR/table10_vr_grab.py`
  does when the mesh is missing.

The seven separate part meshes from the first import are still in the folder's `StaticMeshes`; nothing uses them now.
