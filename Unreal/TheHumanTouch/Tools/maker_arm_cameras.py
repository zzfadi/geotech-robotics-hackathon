"""Three photo-positioned cameras; extrinsics and optics are provisional.

Run setup() in a scoped CoPresence task. Wrist cameras attach to link_007, so
maker_arm_station.set_pose() moves them with the wrist without another update.
"""
import unreal
import yam_station

LABELS = ('MakerCamera_LeftWrist', 'MakerCamera_RightWrist', 'MakerCamera_Overview')


def setup(labels=LABELS):
    existing = yam_station.actors()
    required = ['MakerArm_'+side+'_link_007' for side in ('left', 'right')]
    required.append(yam_station.PREFIX+'OverviewCameraProxy')
    if any(label not in existing for label in required):
        raise ValueError('Complete station is required before adding cameras')
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    specifications = []
    for side, label in zip(('left', 'right'), LABELS[:2]):
        parent = existing['MakerArm_'+side+'_link_007']
        transform = parent.get_actor_transform()
        location = transform.transform_location(unreal.Vector(12, 0, -12))
        target = transform.transform_location(unreal.Vector(-10, 0, -35))
        specifications.append((label, parent, location, target, 90.0))
    parent = existing[yam_station.PREFIX+'OverviewCameraProxy']
    location = parent.get_actor_location() + unreal.Vector(3.5, 0, -3.5)
    specifications.append((LABELS[2], parent, location, unreal.Vector(53, 0, 75), 100.0))
    for label, parent, location, target, fov in specifications:
        if label not in labels: continue
        camera = existing.get(label)
        if camera is None and label == LABELS[2]:
            camera = existing.get(yam_station.PREFIX+'OverviewCamera_Uncalibrated')
        if camera is not None and not isinstance(camera, unreal.CameraActor):
            raise ValueError('Camera label belongs to another actor: '+label)
        if camera is None:
            camera = subsystem.spawn_actor_from_class(unreal.CameraActor, location)
        camera.modify()
        camera.set_actor_label(label)
        camera.set_folder_path('MakerStation/Cameras')
        camera.set_actor_location_and_rotation(location, unreal.MathLibrary.find_look_at_rotation(location, target), False, True)
        camera.set_actor_scale3d(unreal.Vector(1, 1, 1))
        component = camera.get_component_by_class(unreal.CameraComponent)
        component.modify()
        component.set_field_of_view(fov)
        component.set_aspect_ratio(16.0/9.0)
        component.set_editor_property('constrain_aspect_ratio', True)
        rule = unreal.AttachmentRule.KEEP_WORLD
        if not camera.attach_to_actor(parent, '', rule, rule, rule, False):
            raise RuntimeError('Failed camera attachment: '+label)
        camera.set_editor_property('tags', ['MakerStationCamera', 'Uncalibrated'])
    return hide_camera_meshes()


def inspect():
    existing = yam_station.actors()
    result = []
    for label in LABELS:
        actor = existing.get(label)
        if not isinstance(actor, unreal.CameraActor):
            raise ValueError('Missing CameraActor: '+label)
        camera = actor.get_component_by_class(unreal.CameraComponent)
        parent = actor.get_attach_parent_actor()
        result.append({'label': label, 'parent': parent.get_actor_label() if parent else None,
                       'location': str(actor.get_actor_location()),
                       'rotation': str(actor.get_actor_rotation()),
                       'horizontal_fov_degrees': camera.field_of_view,
                       'aspect_ratio': camera.aspect_ratio})
    return result


def refine_central_mount():
    """Match the new front photo: black flat mast, top housing and underside lens.

    Sizes are photo estimates in cm; this is a visual mount, not measured CAD.
    """
    existing = yam_station.actors()
    dark = unreal.load_asset(yam_station.ASSETS+'/M_YAM_Dark')
    if dark is None: raise ValueError('Missing station material')
    for suffix, scale in [('CameraMast', (0.015, 0.07, 0.66)),
                          ('OverviewCameraProxy', (0.06, 0.10, 0.025))]:
        actor = existing[yam_station.PREFIX+suffix]
        actor.modify()
        actor.set_actor_scale3d(unreal.Vector(*scale))
        actor.static_mesh_component.modify()
        actor.static_mesh_component.set_material(0, dark)
    name = 'MakerStation_CentralCameraLens_Approximate'
    lens = existing.get(name)
    if lens is None:
        lens = yam_station.mesh_actor(name, unreal.load_asset('/Engine/BasicShapes/Cylinder.Cylinder'),
                                      (11.7, 0, 139.8), (0.022, 0.022, 0.02), dark)
    lens.modify()
    direction = unreal.Vector(40.5, 0, -63.5)
    lens.set_actor_rotation(unreal.MathLibrary.make_rot_from_z(direction), True)
    lens.set_folder_path('MakerStation/Cameras')
    return setup((LABELS[2],))


def hide_camera_meshes():
    """Hide camera representations without disabling optics or wrist attachments."""
    existing = yam_station.actors()
    for label in LABELS:
        actor = existing[label]
        component = actor.get_component_by_class(unreal.CameraComponent)
        actor.modify()
        component.modify()
        component.set_editor_property('camera_mesh', None)
        component.set_editor_property('camera_mesh_hidden_in_game', True)
        for primitive in actor.get_components_by_class(unreal.PrimitiveComponent):
            primitive.modify()
            primitive.set_visibility(False, False)
            primitive.set_hidden_in_game(True, False)
    for label in (yam_station.PREFIX+'OverviewCameraProxy',
                  'MakerStation_CentralCameraLens_Approximate'):
        proxy=existing.get(label)
        if proxy is not None:
            proxy.modify()
            mesh=proxy.get_component_by_class(unreal.StaticMeshComponent)
            mesh.modify();mesh.set_visibility(False,False);mesh.set_hidden_in_game(True,False)
    return inspect()


def show_central_camera_body():
    """Show only the small physical camera proxy; retain hidden editor icons."""
    existing = yam_station.actors()
    for label in (yam_station.PREFIX+'OverviewCameraProxy',
                  'MakerStation_CentralCameraLens_Approximate'):
        actor = existing.get(label)
        if actor is None: raise ValueError('Missing central camera proxy: '+label)
        actor.modify()
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        component.modify()
        component.set_visibility(True, False)
        component.set_hidden_in_game(False, False)
    return {'central_body_visible': True, 'editor_camera_icons': 'hidden'}
