"""Explicit photo-informed camera/target trial, not measured calibration.
Call capture_before() BEFORE setup motion, then apply() after canonical REST.
This never runs during Dream motion or active physics.
"""
import json,time
from pathlib import Path
import unreal
import maker_arm_feedback
from copresence import animation

CAMERAS=('MakerCamera_LeftWrist','MakerCamera_RightWrist','MakerCamera_Overview')
TARGET='MakerStation_Approximate_Block_Red'
CONTROL='MakerStation_UnverifiedControlProxy'

def actors():return {a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}

def check_idle():
    if animation.is_running() or unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Calibration setup requires idle editor and no simulation')

def capture_before(path):
    check_idle();existing=actors();data={'created_unix':time.time(),'scope':'before explicit provisional calibration trial','robot_state':maker_arm_feedback.read(),'actors':{}}
    for label in [*CAMERAS,TARGET,CONTROL]:
        a=existing[label];t=a.get_actor_transform();p,q,s=t.translation,t.rotation,t.scale3d
        item={'position_cm':[p.x,p.y,p.z],'quaternion_xyzw':[q.x,q.y,q.z,q.w],'scale':[s.x,s.y,s.z]}
        parent=a.get_attach_parent_actor();item['parent']=parent.get_actor_label() if parent else None
        if isinstance(a,unreal.CameraActor):
            c=a.get_component_by_class(unreal.CameraComponent);item.update(fov_degrees=c.field_of_view,aspect_ratio=c.aspect_ratio)
        if isinstance(a,unreal.StaticMeshActor):item['materials']=[m.get_path_name() if m else None for m in a.static_mesh_component.get_materials()]
        data['actors'][label]=item
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():raise RuntimeError('Refusing to overwrite original trial snapshot')
    p.write_text(json.dumps(data,indent=2)+'\n');return data

def apply():
    check_idle();existing=actors();state=maker_arm_feedback.read()
    for side in ('left','right'):
        if max(abs(a-b) for a,b in zip(state['q_'+side],[0,-1.2,.81,-.1,0,0]))>.001:raise RuntimeError('Move to canonical REST before camera trial')
    for label in CAMERAS[:2]:
        camera=existing[label];parent=camera.get_attach_parent_actor()
        if parent is None:raise RuntimeError('Wrist camera is not attached')
        transform=parent.get_actor_transform()
        location=transform.transform_location(unreal.Vector(5,0,-12))
        target=transform.transform_location(unreal.Vector(0,0,-17))
        camera.modify();camera.root_component.modify()
        camera.set_actor_location_and_rotation(location,unreal.MathLibrary.find_look_at_rotation(location,target),False,True)
        c=camera.get_component_by_class(unreal.CameraComponent);c.modify();c.set_field_of_view(90.)
    block=existing[TARGET];block.modify();block.root_component.modify()
    block.set_actor_scale3d(unreal.Vector(.07,.07,.07))
    block.set_actor_location_and_rotation(unreal.Vector(45,-40,78.5),unreal.Rotator(0,0,0),False,True)
    control=existing[CONTROL];control.modify();control.static_mesh_component.modify()
    material=existing['MakerStation_MountingPlate_Approximate'].static_mesh_component.get_material(0)
    if material is None:raise RuntimeError('Missing neutral material')
    control.static_mesh_component.set_material(0,material)
    return {'scope':'explicit provisional camera/target setup, not hardware calibration','wrist_location_local_cm':[5,0,-12],
            'wrist_aim_local_cm':[0,0,-17],'wrist_fov_degrees':90,'red_start_center_cm':[45,-40,78.5],'red_dimensions_cm':[7,7,7],
            'pickup_acceptance_center_z_cm':88.5,'pickup_acceptance':'physical grasp plus >=10cm lift and hold; no attachment or commanded cube path'}


def prepare_tall_grasp():
    """Authorized 5x5x12 cm fixture, open jaws; no runtime object control.

    Invoke inside a scoped CoPresence task while the worker has no active job.
    Source CAD kinematics place the gripper at the block center (60,-40,81) cm.
    """
    check_idle()
    import maker_arm_station
    state={'q_left':[0,-1.9683117433984114,.4323149634697194,1.5359967575109021,0,0],
           'q_right':[0,-1.2,.81,-.1,0,0],'gap_left_m':.08,'gap_right_m':.09}
    block=actors()[TARGET]
    if block.get_attach_parent_actor() is not None:raise RuntimeError('Test block must be unattached')
    for side in ('left','right'):
        maker_arm_station.set_pose(side,state['q_'+side],state['gap_'+side+'_m'])
    block.modify();block.root_component.modify()
    block.set_actor_scale3d(unreal.Vector(.05,.05,.12))
    block.set_actor_location_and_rotation(unreal.Vector(60,-40,81),unreal.Rotator(0,0,0),False,True)
    maker_arm_feedback.seed(state)
    p=Path(unreal.Paths.project_saved_dir())/'DreamDebugger/worker/playback.json'
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({'done':True,'error':None,**state}))
    return {'state':state,'red_dimensions_cm':[5,5,12],'red_center_cm':[60,-40,81],
            'scope':'explicit initial setup only; object remains free during simulation'}
