"""Official CAD triangle collision for position-driven (kinematic) arm links.
This does not provide articulated joint dynamics or calibrated contact properties.
"""
import unreal
from maker_arm_model import model

ROOT='/Game/_Agents/Denys_Mac_Codex/MakerArm'

def configure():
    result=[]
    subsystem=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    for link in model().findall('link'):
        if not link.findall('visual'):continue
        name=link.get('name')
        visual=unreal.load_asset(ROOT+'/SM_MakerArm_'+name)
        collision=unreal.load_asset(ROOT+'/Physics/SM_MakerArm_Collision_'+name)
        if not isinstance(visual,unreal.StaticMesh) or not isinstance(collision,unreal.StaticMesh):raise ValueError('Missing official geometry '+name)
        visual.modify();body=visual.get_editor_property('body_setup');body.modify()
        body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        if not unreal.GeometryScript_Collision.set_static_mesh_custom_complex_collision(visual,collision,True):raise RuntimeError('Could not assign triangle collision '+name)
        if subsystem.get_collision_complexity(visual)!=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE:raise RuntimeError('Collision mode did not persist')
        unreal.EditorAssetLibrary.set_metadata_tag(visual,'ContactModel','Official URDF triangles; kinematic links only; uncalibrated friction')
        result.append(name)
    return {'configured_links':result,'scope':'kinematic triangle contact; dynamic objects require simple collision'}
