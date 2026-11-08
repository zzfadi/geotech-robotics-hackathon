"""Simulation-only wrist framing; not calibrated physical camera extrinsics.

The prior7cm stand-off cannot frame a12cm cup at the grasp center with90deg
horizontal FOV. This extends the virtual sensor stand-off while aiming at the
same official grasp center. Physical camera meshes and robot geometry stay put.
"""
LOCATION_CM=(20.,0.,-12.)
TARGET_CM=(0.,0.,-16.9638704618)
FOV_DEGREES=90.


def apply(indices=range(9)):
    import unreal
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Apply virtual camera framing only while idle')
    aa={a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
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
        location=transform.transform_location(unreal.Vector(*LOCATION_CM))
        target=transform.transform_location(unreal.Vector(*TARGET_CM))
        camera.modify();camera.root_component.modify()
        camera.set_actor_location_and_rotation(location,unreal.MathLibrary.find_look_at_rotation(location,target),False,True)
        component=camera.get_component_by_class(unreal.CameraComponent)
        component.modify();component.set_field_of_view(FOV_DEGREES)
    return {'cameras':[a.get_actor_label() for a in cameras],'local_location_cm':LOCATION_CM,
            'local_aim_cm':TARGET_CM,'hfov_degrees':FOV_DEGREES,'scope':'Uncalibrated virtual optics only'}
