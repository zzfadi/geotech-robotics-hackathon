# Free Wave can (Table 10 handover object)

Denys's model of a 12 oz Athletic Free Wave can, used as the visible can on Table 10
(`MakerInteraction_T09__HandoverCan_Model_*`, seven parts on the invisible 6.6 × 12.2 cm grasp proxy
`MakerInteraction_T09__HandoverCan`).

- `free_wave_can.glb`: the model imported into
  `/Game/_Agents/Denys_Mac_Codex/MakerArm/Cans/FreeWave/SM_FreeWaveCan`.
- `free_wave_can.blend` and `build_can.py`: the Blender source and the script that builds it.
- `label.png`: the printed label texture.

The body object is named "Can body • 355 mL". The bullet becomes part of the asset name on import,
and renaming the asset afterwards saves it, which agent editors refuse. `Tools/QuestVR/table10_vr_grab.py`
therefore imports a copy with the bullet removed, so the body arrives as `Can_body_355_mL`.
