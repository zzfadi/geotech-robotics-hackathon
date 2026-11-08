"""Virtual wrist sensors follow wrist positions with world-down optical axes.

Both images have world +X at the top and +Y at the right, with no side mirror.
The virtual stand-off/FOV remain uncalibrated, not a physical sensor match.
"""
LOCATION_CM=(14.,20.,8.)
TARGET_CM=(10.,0.,-25.)
FOV_DEGREES=110.


def apply(indices=range(9),location_cm=None,target_cm=None):
    import unreal
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Apply virtual camera framing only while idle')
    aa={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    location_cm=LOCATION_CM if location_cm is None else tuple(location_cm)
    target_cm=TARGET_CM if target_cm is None else tuple(target_cm)
    import math
    if len(location_cm)!=3 or len(target_cm)!=3 or not all(math.isfinite(v) for v in (*location_cm,*target_cm)):
        raise ValueError('Expected finite flange-local camera coordinates')
    forward=unreal.Vector(*(target_cm[i]-location_cm[i] for i in range(3)))
    if forward.length()<.001:raise ValueError('Camera aim must differ from location')
    rotation=world_down_rotation()
    cameras=[]
    for index in indices:
        if type(index) is not int or not 0<=index<=8:raise ValueError('Expected Dream table0..8')
        prefix='' if index==0 else 'MakerFleet_T%02d__'%index
        for side in ('Left','Right'):
            camera=aa[prefix+'MakerCamera_'+side+'Wrist']
            if not isinstance(camera,unreal.CameraActor) or camera.get_attach_parent_actor() is None:
                raise ValueError('Missing attached wrist camera')
            cameras.append(camera)
    for camera in cameras:
        transform=camera.get_attach_parent_actor().get_actor_transform()
        location=transform.transform_location(unreal.Vector(*location_cm))
        camera.modify();camera.root_component.modify()
        camera.root_component.set_absolute(False,True,False)
        camera.set_actor_location_and_rotation(location,rotation,False,True)
        component=camera.get_component_by_class(unreal.CameraComponent)
        component.modify();component.set_field_of_view(FOV_DEGREES)
    return {'cameras':[a.get_actor_label() for a in cameras],'local_location_cm':location_cm,
            'local_aim_cm':target_cm,'hfov_degrees':FOV_DEGREES,'scope':'Uncalibrated virtual optics; world-down, image up +X, image right +Y'}


def audit_world_tcp(index,state,world=None):
    """Read actual flange transforms and compare TCP in Dream's RH base frame.

    Supply the authoritative game world during SIE. No scene writes are made.
    The archived fleet run used tcp_back=0, matching the official grasp_center.
    """
    import unreal
    from maker_arm_model import model,mounted_frames,numbers
    from maker_arm_station import BASES,MOUNT_YAW
    from maker_arm_fleet_layout import label,replica_base
    if world is None:world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None:raise RuntimeError('Supply the actual world during SIE')
    actors={a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.StaticMeshActor)}
    xyz=numbers(model().find("joint[@name='grasp_center_joint']/origin").get('xyz'))
    local_tcp=unreal.Vector(100*xyz[0],-100*xyz[1],100*xyz[2])
    result={}
    for side in ('left','right'):
        actor=actors[label(index,'MakerArm_'+side+'_link_007')]
        p=actor.get_actor_transform().transform_location(local_tcp)
        base=replica_base(index,BASES[side])
        observed=[(p.x-base[0])/100,-(p.y-base[1])/100,(p.z-base[2])/100]
        frame=mounted_frames(state['q_'+side],state['gap_'+side+'_m'],yaw=MOUNT_YAW[side])['grasp_center']
        expected=[frame[i][3] for i in range(3)]
        result[side]={'actual_world_tcp_cm':[p.x,p.y,p.z],'world_base_cm':list(base),
                      'observed_dream_base_tcp_m':observed,'reported_joint_fk_tcp_m':expected,
                      'position_error_m':sum((a-b)**2 for a,b in zip(observed,expected))**.5}
    return result


def is_wrist_camera(source):
    label=source.get_actor_label()
    return (label in ('MakerCamera_LeftWrist','MakerCamera_RightWrist') or
            label.startswith('MakerFleet_') and label.endswith(('MakerCamera_LeftWrist','MakerCamera_RightWrist')))


def world_down_rotation():
    import unreal
    # UE camera +X is optical forward, +Y image right, +Z image up.
    return unreal.MathLibrary.make_rot_from_xy(unreal.Vector(0,0,-1),unreal.Vector(0,1,0))


def capture_rotation(source):
    """Enforce actual policy/export optics even if an old actor loses its flag."""
    return world_down_rotation() if is_wrist_camera(source) else source.get_actor_rotation()
