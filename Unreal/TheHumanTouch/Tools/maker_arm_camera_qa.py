"""Local static wrist-camera render checks. No physics or policy calls."""
from pathlib import Path
import unreal
from copresence_call import call_camera
from copresence import ui_widgets
original={}

def _require_idle():
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError("Static camera QA requires idle editor, no SIE")

def snapshot():
    _require_idle()
    global original
    original={a.get_actor_label():a.get_actor_transform() for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label().startswith('MakerArm_left_') or a.get_actor_label() in ('MakerCamera_LeftWrist','MakerCamera_RightWrist')}
    return len(original)

def restore_arms():
    _require_idle()
    actors={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    for name,t in original.items():
        if not name.startswith('MakerArm_left_'):continue
        a=actors[name];a.modify();a.root_component.modify();a.set_actor_transform(t,False,True)

def capture(path,label='MakerCamera_LeftWrist'):
    _require_idle()
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    camera=next(a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label()==label)
    from maker_arm_wrist_framing import capture_rotation
    def create():
        target=unreal.new_object(unreal.TextureRenderTarget2D)
        target.set_editor_property('render_target_format',unreal.TextureRenderTargetFormat.RTF_RGBA8);target.set_editor_property('target_gamma',2.2)
        unreal.RenderingLibrary.resize_render_target2d(target,320,176)
        actor,component=call_camera.spawn_capture_camera(target,camera.get_actor_location(),capture_rotation(camera),camera.get_component_by_class(unreal.CameraComponent).field_of_view)
        component.set_editor_property('override_custom_near_clipping_plane',True);component.set_editor_property('custom_near_clipping_plane',1.)
        for p in actor.get_components_by_class(unreal.PrimitiveComponent):p.set_visibility(False,False);p.set_hidden_in_game(True,False)
        def export():
            try:
                component.capture_scene();options=unreal.ImageWriteOptions();options.set_editor_property('format',unreal.DesiredImageFormat.JPG);options.set_editor_property('compression_quality',90);options.set_editor_property('async_',False);options.set_editor_property('overwrite_file',True)
                unreal.ImageWriteBlueprintLibrary.export_to_disk(target,str(path),options)
            finally:call_camera._remove([actor])
        ui_widgets.after(.1,lambda:component.capture_scene());ui_widgets.after(.3,export)
        return True
    if call_camera._locally(create) is None:raise RuntimeError('Capture after enclosing transaction')
