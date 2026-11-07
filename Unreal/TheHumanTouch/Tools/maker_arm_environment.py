"""Reference-inspired empty industrial hall; run inside a scoped CoPresence task.

Artistic room dimensions/photometry, not a calibrated model of the photographed hall.
Only MakerEnvironment_* and MakerLighting_* actors may be changed.
"""
import unreal
import yam_station

ASSETS = '/Game/_Agents/Denys_Mac_Codex/Environment'
PREFIX = 'MakerEnvironment_'


def _material(name, color):
    path = ASSETS + '/M_' + name
    m = unreal.load_asset(path)
    if m:
        return m
    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        'M_' + name, ASSETS, unreal.Material, unreal.MaterialFactoryNew())
    m.modify()
    base = unreal.MaterialEditingLibrary.create_material_expression(m, unreal.MaterialExpressionConstant3Vector)
    base.set_editor_property('constant', unreal.LinearColor(*color, 1))
    unreal.MaterialEditingLibrary.connect_material_property(base, '', unreal.MaterialProperty.MP_BASE_COLOR)
    rough = unreal.MaterialEditingLibrary.create_material_expression(m, unreal.MaterialExpressionConstant)
    rough.set_editor_property('r', .85)
    unreal.MaterialEditingLibrary.connect_material_property(rough, '', unreal.MaterialProperty.MP_ROUGHNESS)
    unreal.MaterialEditingLibrary.recompile_material(m)
    return m


def protected_state():
    return {a.get_actor_label(): [a.get_actor_location().x, a.get_actor_location().y, a.get_actor_location().z,
            a.get_actor_rotation().pitch, a.get_actor_rotation().yaw, a.get_actor_rotation().roll,
            a.get_actor_scale3d().x, a.get_actor_scale3d().y, a.get_actor_scale3d().z] for a in
            unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
            if a.get_actor_label().startswith(('MakerArm_', 'MakerStation_', 'MakerCamera_', 'Dreamlabs_YAM_'))}


def setup():
    before = protected_state()
    existing = yam_station.actors()
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    def actor(name, kind, pos):
        label = PREFIX + name
        a = existing.get(label)
        if a is None:
            a = subsystem.spawn_actor_from_class(kind, unreal.Vector(*pos))
        if not isinstance(a, kind):
            raise ValueError('Unexpected environment actor type: ' + label)
        a.modify()
        a.set_actor_label(label)
        a.set_folder_path('MakerStation/Environment')
        a.set_actor_location(unreal.Vector(*pos), False, True)
        return a
    floor = _material('HallFloor', (.24, .225, .20))
    plaster = _material('HallPlaster', (.70, .72, .70))
    fixture = _material('HallFixture', (.035, .04, .045))
    cube = unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    for name, pos, scale, mat in (
        ('Floor', (45, 0, -2), (8, 8, .04), floor),
        ('BackWall', (445, 0, 200), (.08, 8, 4), plaster),
        ('RearWall', (-355, 0, 200), (.08, 8, 4), plaster),
        ('LeftWall', (45, -400, 200), (8, .08, 4), plaster),
        ('RightWall', (45, 400, 200), (8, .08, 4), plaster),
        ('BeamLeft', (45, -230, 385), (8, .12, .24), plaster),
        ('BeamRight', (45, 230, 385), (8, .12, .24), plaster),
        ('StripHousingLeft', (45, -140, 330), (.12, 2, .04), fixture),
        ('StripHousingRight', (45, 140, 330), (.12, 2, .04), fixture)):
        a = actor(name, unreal.StaticMeshActor, pos)
        c = a.static_mesh_component
        c.modify()
        c.set_mobility(unreal.ComponentMobility.MOVABLE)
        c.set_static_mesh(cube)
        c.set_material(0, mat)
        c.set_collision_profile_name('BlockAll' if name == 'Floor' else 'NoCollision')
        c.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS if name == 'Floor' else unreal.CollisionEnabled.NO_COLLISION)
        a.set_actor_scale3d(unreal.Vector(*scale))
    for name, pos, target, power, width, height, kelvin in (
        ('WindowFill', (45, -350, 220), (45, 0, 80), 9000, 300, 220, 6500),
        ('CeilingLeft', (45, -140, 325), (45, -140, 0), 3500, 200, 12, 4500),
        ('CeilingRight', (45, 140, 325), (45, 140, 0), 3500, 200, 12, 4500),
        ('RoomBounce', (45, 0, 270), (-355, 0, 170), 12000, 300, 250, 5500)):
        a = actor(name, unreal.RectLight, pos)
        a.set_actor_rotation(unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*pos), unreal.Vector(*target)), True)
        c = a.get_component_by_class(unreal.RectLightComponent)
        c.modify()
        c.set_mobility(unreal.ComponentMobility.MOVABLE)
        c.set_editor_property('intensity_units', unreal.LightUnits.LUMENS)
        c.set_intensity(power)
        c.set_editor_property('source_width', width)
        c.set_editor_property('source_height', height)
        c.set_editor_property('attenuation_radius', 1000)
        c.set_editor_property('use_temperature', True)
        c.set_temperature(kelvin)
        c.set_cast_shadows(True)
    ambient = existing.get('MakerLighting_Ambient')
    if ambient:
        ambient.modify()
        ambient.light_component.modify()
        ambient.light_component.set_intensity(1.1)
    if protected_state() != before:
        raise RuntimeError('Protected station transform changed during environment setup')
    return {'environment_actors': 13, 'floor_top_cm': 0, 'room_m': [8, 8, 4],
            'protected_transforms_unchanged': True, 'humans_added': 0,
            'scope': 'reference-inspired scenery and lighting; uncalibrated'}


def add_window_context():
    """Optional second visual pass; call separately in a coordinated editor task.

    Shallow window panels sit inside the left wall, representing the reference's
    high daylight windows without changing the station or requiring wall cutouts.
    """
    before = protected_state()
    existing = yam_station.actors()
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    pane = _material('HallDaylightPane', (.57, .73, .82))
    trim = _material('HallWindowTrim', (.76, .78, .76))
    cube = unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    count = 0
    for index, x in enumerate((-160, 45, 250)):
        for suffix, pos, scale, mat in (
            ('Pane', (x, -395, 205), (1.4, .01, 1.6), pane),
            ('Top', (x, -393, 287), (1.48, .06, .04), trim),
            ('Sill', (x, -392, 123), (1.48, .10, .04), trim),
            ('Left', (x-72, -393, 205), (.04, .06, 1.64), trim),
            ('Right', (x+72, -393, 205), (.04, .06, 1.64), trim),
            ('Mullion', (x, -392, 205), (.035, .07, 1.6), trim),
            ('Transom', (x, -392, 205), (1.4, .07, .035), trim)):
            label = PREFIX + 'Window%d_' % index + suffix
            a = existing.get(label)
            if a is None:
                a = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*pos))
            if not isinstance(a, unreal.StaticMeshActor):
                raise ValueError('Unexpected window actor type: ' + label)
            a.modify()
            a.set_actor_label(label)
            a.set_folder_path('MakerStation/Environment/Windows')
            a.set_actor_location(unreal.Vector(*pos), False, True)
            a.set_actor_scale3d(unreal.Vector(*scale))
            c = a.static_mesh_component
            c.modify()
            c.set_mobility(unreal.ComponentMobility.MOVABLE)
            c.set_static_mesh(cube)
            c.set_material(0, mat)
            c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            count += 1
    if protected_state() != before:
        raise RuntimeError('Protected station transform changed during window setup')
    return {'window_context_actors': count, 'protected_transforms_unchanged': True,
            'scope': 'artistic shallow daylight-window representation'}
