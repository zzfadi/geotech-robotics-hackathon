"""Restore the saved scan-room character demo in the solo editor."""
import unreal
PATH='/Game/Scans/Animations/LS_RoomHuman'
def start():
 world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
 if not world or not world.get_path_name().startswith('/Game/Scans/Maps/Room_20261027_Preview.'):return False
 E=unreal.LevelSequenceEditorBlueprintLibrary
 seq=unreal.load_asset(PATH)
 if not seq:return False
 current=E.get_current_level_sequence()
 if current and current!=seq:return False
 for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
  if a.get_actor_label()=='Room_MetaHuman':
   for c in a.get_components_by_class(unreal.SkeletalMeshComponent):c.set_update_animation_in_editor(True)
 E.open_level_sequence(seq);E.set_loop_mode(unreal.SequencerLoopMode.SLM_LOOP);E.play()
 return E.is_playing()
